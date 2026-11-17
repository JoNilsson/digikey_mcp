# BOM ingest

Reads a bill of materials in any common form and returns normalised
`BomLine` records for a downstream part resolver.

```python
from digikey_mcp.ingest import ingest

result = ingest("bom.xlsx")                       # path, bytes or pasted text
result = ingest(text, fmt="text")                 # force a format
result = ingest(path, mapping={"Teile Nr": "mpn", 3: "value"})  # header or column index -> field

result.lines            # list[BomLine]
result.mapping_report   # header -> field, unmapped headers, missing key fields, header row
result.unparsed_rows    # rows that were not turned into a line, each with a reason
result.warnings         # duplicate designators, decoding fallbacks, ignored sheets ...
result.preamble         # non-blank rows above the header (tool name, project, date)
```

## Inputs

| Format | Detection | Notes |
| --- | --- | --- |
| CSV / TSV | extension or content; `,` `;` tab `\|` delimiters | header row is found among the first 40 rows, so tool preambles are skipped |
| KiCad XML | `<comp>` elements | `ref`, value, footprint, `fields`, `libsource` description, `dnp` and `exclude_from_bom` properties |
| other XML | most repeated record element | attributes and leaf children become columns |
| Excel `.xlsx` | zip magic or extension | needs `openpyxl`; the sheet with the best header is read |
| PDF | `%PDF` magic or extension | needs `pdfplumber` (tables, then text) or `pypdf` (text) |
| pasted text | fallback | delimited, space-aligned, or header-less lines starting with designators |

Optional dependencies are imported on first use; a missing one raises
`MissingDependencyError` naming the package. Legacy `.xls` is rejected.

## BomLine

`line_id`, `refdes` (expanded, natural order), `quantity`, `value` (canonical),
`value_raw`, `value_si`, `value_unit`, `tolerance`, `footprint`, `package`,
`description`, `mpn`, `manufacturer`, `supplier_pn` (DigiKey part number),
`dnp`, `category_hint`, `generic`, `raw`, `source_row`, `source_rows`, `notes`.

* Rows that share an MPN (or a DigiKey number, or category + value + footprint
  when neither exists) and the same DNP state merge into one line. Designators
  are united and quantities summed.
* `quantity` comes from the quantity column, else from the designator count,
  else 1 with a note. A mismatch with the designator count is noted.
* `generic` is true when there is neither an MPN nor a DigiKey number.
* A DigiKey-pattern number (`296-8875-1-ND`) found in an MPN column is moved to
  `supplier_pn`. Other suppliers' numbers are noted, not used.
* A part number cell with several numbers keeps the first; the rest are noted.

## DNP and non-parts

DNP, DNF, DNI, "do not populate/fit", "not fitted" and similar markers are
detected in DNP/populate/exclude columns, in value, description and designator
cells and in note-like columns. Such lines keep `dnp=True` and are never
dropped. Fiducials, mounting holes and test points get the categories
`fiducial`, `mounting_hole` and `test_point` and are never dropped. Use
`BomLine.orderable`, `IngestResult.dnp_lines` and `non_part_lines` to separate
them.

## Values

`normalise_value` converts `4k7`, `4.7k`, `4,7k` to `4.7kohm`; `0.1uF` and
`1000nF` to `100nF` and `1uF`; `0R22` to `0.22ohm`. Tolerance, voltage,
dielectric and power tokens (`1%`, `50V`, `X7R`, `1/4W`) are split from the
value. Text that is not a number (`LM358`) is kept as is. The category (from
designator prefix, footprint library, value unit, description) disambiguates
bare numbers.

## Categories

`resistor`, `capacitor`, `inductor`, `connector`, `ic`, `diode`, `led`,
`transistor`, `crystal`, `switch`, `fuse`, `fiducial`, `mounting_hole`,
`test_point`, `other`.

## Free text

Without a header, a line is accepted only when it starts with known
designators (`R1, R2`, `C1-C3`) or a quantity (`4x`) and carries at least a
value, package or DigiKey number. Everything else, including bare numbers that
could be a value or a quantity, goes to `unparsed_rows`.

The designator prefix sets the category only for a real designator: letters,
then digits (`R12`, `FU1`), with a unit letter after at most three digits
(`U2A`). Module names and labels (`D1608E`, `A0602`, `OPTA`, `GA-IS`) give no
prefix category. On a line with a manufacturer or DigiKey part number, a prefix
alone does not give resistor, capacitor, inductor, LED, diode or transistor (the
categories that carry spare-part overage); the footprint, description or a
passive value must.
