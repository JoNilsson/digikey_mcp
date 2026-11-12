{
  description = "DigiKey Product Search client and MCP server";

  # flake.lock pins the same nixpkgs revision as cybernix. Unstable from
  # 2026-11 ships mcp 2.x, which marks fastmcp(-slim) broken; check
  # `nix eval .#packages.x86_64-linux.default.drvPath` after any update.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python3;

      # Runtime dependencies, mirroring [project].dependencies in pyproject.toml.
      # Add new entries here (e.g. ps.openpyxl, ps.pdfplumber, ps.pypdf) when
      # pyproject.toml gains them.
      runtimeDeps = ps: [
        ps.fastmcp
        ps.httpx
      ];

      # Optional dependency groups, mirroring [project.optional-dependencies].
      # Each group is built into the package as `optional-dependencies` and can
      # be pulled in with `withOptional [ "ingest" ]`. Empty until pyproject
      # declares them; an example for the planned BOM ingest:
      #   ingest = ps: [ ps.openpyxl ps.pdfplumber ps.pypdf ];
      optionalDeps = { };

      testDeps = ps: [ ps.pytest ];

      digikey-mcp = python.pkgs.buildPythonApplication {
        pname = "digikey-mcp";
        version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
        pyproject = true;
        src = self;

        build-system = [ python.pkgs.hatchling ];
        dependencies = runtimeDeps python.pkgs;
        optional-dependencies = builtins.mapAttrs (_: f: f python.pkgs) optionalDeps;

        # Tests are offline (httpx MockTransport, in-process FastMCP client).
        nativeCheckInputs = testDeps python.pkgs;
        checkPhase = ''
          runHook preCheck
          pytest -q tests
          runHook postCheck
        '';
        pythonImportsCheck = [ "digikey_mcp" ];

        meta = {
          description = "DigiKey Product Search client and MCP server";
          mainProgram = "digikey-mcp";
        };
      };

      # Package with selected optional groups added to the runtime closure.
      withOptional =
        groups:
        digikey-mcp.overridePythonAttrs (old: {
          dependencies =
            old.dependencies ++ builtins.concatMap (g: old.optional-dependencies.${g}) groups;
        });

      devPython = python.withPackages (
        ps:
        runtimeDeps ps
        ++ builtins.concatLists (builtins.attrValues (builtins.mapAttrs (_: f: f ps) optionalDeps))
        ++ testDeps ps
      );

      # Home Manager module: registers the server for Claude Code. Credentials
      # stay outside the store; only their file paths are passed, through
      # CLIENT_ID_FILE / CLIENT_SECRET_FILE read by client.load_credentials.
      homeModule =
        {
          config,
          lib,
          options,
          pkgs,
          ...
        }:
        let
          cfg = config.services.digikey-mcp;
          server = {
            type = "stdio";
            command = lib.getExe cfg.package;
            args = [ ];
            env = {
              CLIENT_ID_FILE = cfg.clientIdFile;
              CLIENT_SECRET_FILE = cfg.clientSecretFile;
            }
            // lib.optionalAttrs cfg.sandbox { DIGIKEY_SANDBOX = "true"; };
          };
        in
        {
          options.services.digikey-mcp = {
            enable = lib.mkEnableOption "the DigiKey MCP server for Claude Code";
            package = lib.mkOption {
              type = lib.types.package;
              default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
              defaultText = lib.literalExpression "digikey-mcp.packages.\${system}.default";
              description = "digikey-mcp package; use lib.\${system}.withOptional for extra groups.";
            };
            # Strings, not paths: a path literal would be copied into the store.
            clientIdFile = lib.mkOption {
              type = lib.types.str;
              example = "/run/secrets/digikey/client_id";
              description = "File holding the DigiKey OAuth client id (sops-nix, agenix, ...).";
            };
            clientSecretFile = lib.mkOption {
              type = lib.types.str;
              example = "/run/secrets/digikey/client_secret";
              description = "File holding the DigiKey OAuth client secret.";
            };
            sandbox = lib.mkOption {
              type = lib.types.bool;
              default = false;
              description = "Use the DigiKey sandbox API (sets DIGIKEY_SANDBOX=true).";
            };
            serverName = lib.mkOption {
              type = lib.types.str;
              default = "digikey";
              description = "Name of the MCP server entry in Claude Code.";
            };
          };

          config = lib.mkIf cfg.enable (
            lib.mkMerge [
              {
                home.packages = [ cfg.package ];
                # Standalone config for `claude --mcp-config <file>` or for
                # merging into a managed MCP config.
                xdg.configFile."digikey-mcp/mcp.json".text = builtins.toJSON {
                  mcpServers.${cfg.serverName} = server;
                };
              }
              # Home Manager's own Claude Code module, when present, picks the
              # server up directly.
              (lib.optionalAttrs (options ? programs.claude-code.mcpServers) {
                programs.claude-code.mcpServers.${cfg.serverName} = server;
              })
            ]
          );
        };
    in
    {
      packages.${system} = {
        default = digikey-mcp;
        inherit digikey-mcp;
      };

      lib.${system}.withOptional = withOptional;

      apps.${system}.default = {
        type = "app";
        program = "${digikey-mcp}/bin/digikey-mcp";
        meta.description = "Run the DigiKey MCP server on stdio";
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          devPython
          pkgs.ruff
          pkgs.pyright
        ];
        # Import the package from the working tree without an editable install.
        shellHook = ''
          export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
        '';
      };

      homeManagerModules.default = homeModule;
    };
}
