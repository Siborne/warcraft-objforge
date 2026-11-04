# 物编工坊 · ObjForge

Warcraft III 物编数据与 Excel/CSV 双向转换工具。支持单位、技能、物品和科技的完整属性导出、表格编辑与二进制回写。

工具依赖 Python 3.8 以上版本，不需要安装第三方库。

## 图形面板

双击 `打开W3U图形面板.bat`，或在 PowerShell 中运行：

```powershell
python .\w3u_gui.py
```

面板支持 `.w3u`、`.w3a`、`.w3t`、`.w3q`。选择原始文件后会根据扩展名切换对应的物编元数据和输出扩展名。四种格式都导出完整属性视图：W3U 每个单位一行；W3T 每个物品一行；W3A/W3Q 每个对象、每个等级一行，保留技能的等级和 Data 通道。

表格文件支持 `.xlsx` 和 `.csv`。推荐使用 `.xlsx`：第一行是英文字段编码与英文名，第二行是中文属性名；表头使用深绿/浅绿配色，原版与自定义对象使用不同底色，并冻结前两行和识别列。XLSX 可以直接回写，不只是查看副本。

物编基础数据默认从项目相对目录 `zhCN-1.27.0\mpq\Units` 加载。点击该目录框或右侧“浏览文件夹…”按钮都可以选择其他数据文件夹。三个文件选择框（原始物编文件、表格文件、输出文件）启动时均为空，避免误选测试文件。导出表格后，编辑并保存，再选择表格和输出路径；回写按钮与“另存为…”并排显示。

## 快速流程：表格 → 物编文件 → 地图编辑器

在 PowerShell 中进入工具目录：

```powershell
cd S:\game\war3\map\the_last_forest\ds
```

### 1. 选择原始 W3U 和项目内的物编数据

在面板中选择要处理的 `.w3u`、`.w3a`、`.w3t` 或 `.w3q`。默认数据目录为：

```powershell
.\zhCN-1.27.0\mpq\Units
```

也可在面板中选择其他包含 `UnitMetaData.slk` 的版本目录。

### 2. 导出并编辑表格

在面板指定表格输出位置并点“导出表格”。建议选择 `.xlsx` 获得美化格式；W3U 导出包含基础值的完整单位属性，W3T 导出完整物品属性，W3A/W3Q 按对象和等级导出完整属性。打开文件后修改数据行中的属性值；保留前两行表头和 `table`、`object_index`、`object_id`、`base_id`、`level` 等识别列。不要排序、改写或删除识别列。XLSX 和 CSV 都可以直接回写，CSV 兼容 UTF-8 及 WPS 常用的 GBK/ANSI 编码。

W3A/W3T/W3Q 的命令行完整导出示例：

```powershell
python .\w3u_tool.py mod-export .\技能.w3a .\技能.csv --data .\zhCN-1.27.0\mpq\Units
python .\w3u_tool.py mod-export .\物品.w3t .\物品.csv --data .\zhCN-1.27.0\mpq\Units
python .\w3u_tool.py mod-export .\科技.w3q .\科技.csv --data .\zhCN-1.27.0\mpq\Units
```

### 3. 将修改复原为对应格式

`.w3u` 使用完整属性回写命令：

```powershell
python .\w3u_tool.py apply-full-csv ".\原始单位.w3u" ".\编辑后的单位.csv" ".\可导入单位.w3u" --data ".\zhCN-1.27.0\mpq\Units"
```

`.w3a`、`.w3t`、`.w3q` 使用修改项回写命令，输入与输出扩展名必须相同：

```powershell
python .\w3u_tool.py apply-mod-csv .\技能.w3a .\技能.csv .\技能-已修改.w3a --data .\zhCN-1.27.0\mpq\Units
python .\w3u_tool.py apply-mod-csv .\物品.w3t .\物品.csv .\物品-已修改.w3t --data .\zhCN-1.27.0\mpq\Units
python .\w3u_tool.py apply-mod-csv .\科技.w3q .\科技.csv .\科技-已修改.w3q --data .\zhCN-1.27.0\mpq\Units
```

在面板中点“表格复原为 W3U/W3A/W3T/W3Q”可完成对应操作。选择导出表格时使用的原始物编文件、编辑后的 XLSX/CSV 和新的输出路径。原始文件不会被覆盖。

### 4. 在地图编辑器中导入并保存地图

打开目标地图，在物体编辑器中选择“文件 → 导入单位设置（Import Unit Settings）”，选中刚生成的 `.w3u`。导入后保存地图。

这份 `.w3u` 直接由你在地图编辑器中导入；工具不会自行修改或打包 `.w3x`。

属性列以 FourCC 编码区分，因此同名的 X/Y 或等级字段不会互相覆盖。未编辑 CSV 的回写结果与原始 W3U 字节完全一致。

## 最常用的四步

### 1. 先验证原始文件是否能无损解析

```powershell
cd S:\game\war3\map\the_last_forest\ds
python .\w3u_tool.py verify .\1.w3u
```

如果输出：

```text
round-trip OK
```

说明解析后再生成的文件与原始文件完全相同。

### 2. 导出为 JSON 和 CSV

```powershell
python .\w3u_tool.py export .\1.w3u
```

会生成：

```text
1.w3u.json
1.w3u.csv
```

其中：

- `1.w3u.json` 是完整数据，适合程序处理，也适合精确修改。
- `1.w3u.csv` 可以用 Excel 打开，适合查看单位、字段和值。

普通 CSV 只列出 `.w3u` 实际保存的改动项。如果要像物编窗口那样看到继承自原始单位的完整属性，运行 `full-export`，并传入当前编辑器版本配套的 `mpq\units` 数据目录：

```powershell
python .\w3u_tool.py full-export .\1.w3u .\1.w3u.full.v3.csv --data ".\zhCN-1.27.0\mpq\Units"
```

`1.w3u.full.v3.csv` 每个单位占一行，属性列合并基础数据和 `.w3u` 覆盖值；覆盖值优先。属性列用物编字段编码区分，同名的 X/Y 或等级字段不会互相覆盖。编辑时只改属性值列；保留 `table`、`object_id`、`base_id` 和字段标题。

### 3. 修改数据

#### 修改单位编码

不建议直接改 JSON 后忘记其他引用。推荐使用：

```powershell
python .\w3u_tool.py rename .\1.w3u .\1.modified.w3u h000=H001
```

可以一次改多个：

```powershell
python .\w3u_tool.py rename .\1.w3u .\1.modified.w3u h000=H001 h001=H002 h002=H003
```

如果字段中还有单位列表、技能列表等文本也引用了旧编码，加上：

```powershell
python .\w3u_tool.py rename .\1.w3u .\1.modified.w3u h000=H001 --replace-text
```

#### 修改物体编辑器字段值

先打开 `1.w3u.json`，找到对应单位和字段，修改 `value`。

例如：

```json
{
  "field_id": "unam",
  "type": 3,
  "value": "我的步兵"
}
```

只改 `value`，不要改 `raw_hex`。工具会检测到值发生变化，并重新编码。

修改完成后执行：

```powershell
python .\w3u_tool.py build .\1.w3u.json .\1.modified.w3u
```

如果目标文件已经存在，需要加 `--force`：

```powershell
python .\w3u_tool.py build .\1.w3u.json .\1.modified.w3u --force
```

#### 修改 CSV

也可以直接编辑 `1.w3u.csv`，然后执行：

```powershell
python .\w3u_tool.py apply-csv .\1.w3u .\1.w3u.csv .\1.modified.w3u
```

CSV 中常改的列：

- `new_id`：自定义单位的新编码。
- `field_id`：字段编码。
- `type`：字段类型编号。
- `value`：字段值。
- `level`：技能/升级等级；`.w3u` 通常为空。
- `data_pointer`：技能数据指针；`.w3u` 通常为空。

如果编辑的是完整物编表 `1.w3u.full.v3.csv`，改完后执行下面命令，生成新的 `1.w3u.import.w3u`：

```powershell
python .\w3u_tool.py apply-full-csv .\1.w3u .\1.w3u.full.v3.csv .\1.w3u.import.w3u --data ".\zhCN-1.27.0\mpq\Units"
```

工具会把 CSV 值和原始 W3U 当前值比较，只写入实际改动；改回默认值的字段会移除覆盖项。原始 `1.w3u` 不会被覆盖。生成的 `1.w3u.import.w3u` 由你在地图编辑器中导入。

注意：`apply-full-csv` 的输入应是由同一个原始 `.w3u` 导出的完整 CSV。若你改了单位编码，CSV 中 `object_id`/`base_id` 列也不要改；编码重命名仍用 `rename` 命令处理。

### 4. 验证生成结果

```powershell
python .\w3u_tool.py verify .\1.modified.w3u
```

也可以查看结构：

```powershell
python .\w3u_tool.py inspect .\1.modified.w3u
```

## W3U 文件结构

文件由以下部分组成：

```text
int32 version
object_table original
object_table custom
```

每个表：

```text
int32 object_count
object_count 个对象
```

每个对象：

```text
char[4] original_id
char[4] new_id
int32 modification_count
modification_count 个修改项
```

每个修改项：

```text
char[4] field_id
int32 type
typed value
int32 end
```

`.w3u` 的修改项没有额外的 `level` 和 `data_pointer` 整数。`.w3a`、`.w3d`、`.w3q` 才会有这两个整数。

## 值类型

| 编号 | 类型 | 当前工具处理方式 |
|---:|---|---|
| 0 | int | 4 字节整数 |
| 1 | real | 4 字节浮点数 |
| 2 | unreal | 4 字节浮点数 |
| 3 | string | 以 `\0` 结尾的字符串 |
| 4 | bool | 4 字节整数 |
| 5 | char | 4 字节原始值 |
| 6-21 | 列表、枚举、路径、技能等 | 以 `\0` 结尾的字符串/列表 |

工具会把原始字段值同时保存在 `raw_hex` 中。没有修改的值写回时优先使用原始字节，因此可以实现无损往返。

## 安全建议

1. 不要覆盖原始 `1.w3u`，先生成 `1.modified.w3u`。
2. 每次生成后执行 `verify`。
3. 改自定义单位编码后，如果地图中已有放置单位、触发器、JASS、技能或物品列表引用旧编码，要同步替换引用。
4. 修改后的 `.w3u` 可在物体编辑器的“文件 → 导入单位设置”中导入目标地图，然后保存地图。

## 支持的其他物体文件

物编转换面板支持完整导出/回写：

```text
.w3u  单位完整属性
.w3a  技能完整属性，按对象和等级展开
.w3t  物品完整属性
.w3q  科技完整属性，按对象和等级展开
```

底层解析器也能解析以下对象文件并通过 `inspect`、`export`、`verify` 处理：

```text
.w3u  单位
.w3t  物品
.w3b  可破坏物
.w3d  装饰物
.w3a  技能
.w3h  魔法效果
.w3q  科技
```

例如：

```powershell
python .\w3u_tool.py export .\war3map.w3a
```
