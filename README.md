# 物编工坊 · ObjForge

Warcraft III 物编数据与 Excel/CSV 双向转换工具。

ObjForge exports Warcraft III object data to editable, styled spreadsheets and rebuilds binary object files after editing.

## Features

- Supports `.w3u`, `.w3a`, `.w3t` and `.w3q`.
- Merges inherited SLK/TXT data with object-file overrides.
- Exports complete unit, ability, item and upgrade properties.
- Preserves ability/upgrade levels and ability data channels.
- Styled `.xlsx` output with English and Chinese header rows, frozen panes, filters and colour-coded sections.
- `.xlsx` and `.csv` can both be imported back into a new object-data file.
- Lossless round trip when the exported table is not modified.
- Windows GUI built with Tkinter; no third-party Python packages required.

## Requirements

- Windows
- Python 3.8 or later
- Warcraft III object editor data containing the matching `*MetaData.slk` and base SLK/TXT files

The repository does not include Warcraft III game data or map test fixtures.

## Run

```powershell
python .\w3u_gui.py
```

Or double-click:

```text
打开W3U图形面板.bat
```

On first launch:

1. Select the source `.w3u`, `.w3a`, `.w3t` or `.w3q` file.
2. Select the data folder containing files such as `UnitMetaData.slk`, `AbilityMetaData.slk` and `UpgradeMetaData.slk`.
3. Choose an `.xlsx` or `.csv` output path.
4. Export, edit the table, then rebuild a new object-data file.
5. Import the generated file with the corresponding World Editor command.

## Command line

```powershell
python .\w3u_tool.py full-export .\单位.w3u .\单位.xlsx --data .\zhCN-1.27.0\mpq\Units
python .\w3u_tool.py apply-full-csv .\单位.w3u .\单位.xlsx .\单位-已修改.w3u --data .\zhCN-1.27.0\mpq\Units

python .\w3u_tool.py mod-export .\技能.w3a .\技能.xlsx --data .\zhCN-1.27.0\mpq\Units
python .\w3u_tool.py apply-mod-csv .\技能.w3a .\技能.xlsx .\技能-已修改.w3a --data .\zhCN-1.27.0\mpq\Units
```

Use `.w3t` for items and `.w3q` for upgrades with the same `mod-export` / `apply-mod-csv` commands.

## Notes

- The source object file is never overwritten.
- Keep identifier columns unchanged when editing exported tables.
- Import the rebuilt file through World Editor, then save the map.
- Warcraft III game data and map files are intentionally excluded from this repository.

See [W3U工具使用说明.md](W3U工具使用说明.md) for the complete Chinese guide.
