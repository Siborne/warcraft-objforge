#!/usr/bin/env python3
"""Windows Tkinter panel for Warcraft object-data table workflows."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

import w3u_tool as core


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path("zhCN-1.27.0") / "mpq" / "Units"


class W3UGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("物编工坊 · ObjForge")
        self.root.geometry("940x760")
        self.root.minsize(820, 680)
        self.root.configure(bg="#e9eee8")

        self.source_var = tk.StringVar(value="")
        self.data_var = tk.StringVar(value=str(DEFAULT_DATA_DIR))
        self.csv_var = tk.StringVar(value="")
        self.output_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="准备就绪")
        self.busy = False
        self.events: queue.Queue = queue.Queue()

        self._setup_style()
        self._build()
        self._poll_events()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh_format_controls()

    def _setup_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "TButton",
            font=("Microsoft YaHei UI", 10),
            padding=(12, 8),
            background="#dce6dc",
            foreground="#1e332b",
        )
        style.map("TButton", background=[("active", "#c8d8cb")])
        style.configure(
            "Accent.TButton",
            background="#1d624d",
            foreground="#ffffff",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#174d3d")])
        style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            foreground="#24362f",
            padding=7,
        )
        style.configure(
            "TLabel",
            background="#e9eee8",
            foreground="#263a32",
            font=("Microsoft YaHei UI", 10),
        )

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg="#e9eee8", padx=28, pady=24)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg="#e9eee8")
        header.pack(fill="x", pady=(0, 18))
        tk.Label(
            header,
            text="OBJFORGE",
            bg="#e9eee8",
            fg="#1d624d",
            font=("Bahnschrift SemiBold", 12),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="物编工坊",
            bg="#e9eee8",
            fg="#172920",
            font=("Microsoft YaHei UI", 23, "bold"),
        ).pack(anchor="w", pady=(3, 2))
        tk.Label(
            header,
            text="Warcraft III Object Data Studio",
            bg="#e9eee8",
            fg="#607269",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w")

        self._section_label(outer, "01  输入文件")
        self.source_entry, _ = self._path_row(
            outer,
            "原始物编文件",
            self.source_var,
            "浏览…",
            self._browse_source,
        )
        self.data_entry, _ = self._path_row(
            outer,
            "物编数据文件夹",
            self.data_var,
            "浏览文件夹…",
            self._browse_data,
        )
        self.data_entry.configure(state="readonly")
        self.data_entry.bind("<Button-1>", self._open_data_picker)

        self.export_section = self._section_label(outer, "02  导出物编表")
        card = self._card(outer)
        self.export_hint = tk.Label(
            card,
            text="选择 W3U 可导出完整单位属性；W3A/W3T/W3Q 导出带字段名的修改项。",
            bg="#ffffff",
            fg="#687a70",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        self.export_hint.pack(fill="x", padx=15, pady=(12, 7))
        self.csv_entry, self.export_button = self._path_row(
            card,
            "表格文件",
            self.csv_var,
            "另存为…",
            self._save_csv_dialog,
            nested=True,
            action_text="导出表格",
            action_command=self._export_csv,
        )

        self._section_label(outer, "03  回写并导入")
        card = self._card(outer)
        tk.Label(
            card,
            text="在 WPS/Excel 改完 XLSX 或 CSV 后，回写会生成与输入格式相同的新物编文件，不会覆盖原文件。",
            bg="#ffffff",
            fg="#687a70",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x", padx=15, pady=(12, 7))
        self.output_entry, self.rebuild_button = self._path_row(
            card,
            "输出文件",
            self.output_var,
            "另存为…",
            self._save_w3u_dialog,
            nested=True,
            action_text="表格复原为文件",
            action_command=self._rebuild,
        )

        import_hint = tk.Frame(outer, bg="#dce9df", padx=14, pady=10)
        import_hint.pack(fill="x", pady=(14, 12))
        tk.Label(
            import_hint,
            text="最后一步：在物体编辑器选择对应格式的导入命令，导入生成文件并保存地图。",
            bg="#dce9df",
            fg="#23543f",
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
            justify="left",
        ).pack(fill="x")

        log_head = tk.Frame(outer, bg="#e9eee8")
        log_head.pack(fill="x", pady=(1, 5))
        tk.Label(
            log_head,
            text="运行记录",
            bg="#e9eee8",
            fg="#40564a",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left")
        self.progress = ttk.Progressbar(log_head, mode="indeterminate", length=110)
        self.progress.pack(side="right")

        log_frame = tk.Frame(outer, bg="#182920", padx=1, pady=1)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_frame,
            height=6,
            bg="#182920",
            fg="#d8e5da",
            insertbackground="#ffffff",
            relief="flat",
            wrap="word",
            font=("Cascadia Mono", 9),
            padx=11,
            pady=9,
            state="disabled",
        )
        self.log.pack(fill="both", expand=True)
        self._log("面板已就绪。导出后编辑表格，再用同一份原始文件回写。")

        footer = tk.Frame(outer, bg="#e9eee8")
        footer.pack(fill="x", pady=(8, 0))
        tk.Label(
            footer,
            textvariable=self.status_var,
            bg="#e9eee8",
            fg="#607269",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        ttk.Button(footer, text="打开使用说明", command=self._open_guide).pack(
            side="right"
        )

    def _section_label(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg="#e9eee8",
            fg="#395247",
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        )
        label.pack(fill="x", pady=(5, 7))
        return label

    def _card(self, parent: tk.Widget) -> tk.Frame:
        card = tk.Frame(parent, bg="#ffffff", highlightthickness=1, highlightbackground="#d3ded5")
        card.pack(fill="x", pady=(0, 8))
        return card

    def _path_row(
        self,
        parent: tk.Widget,
        label: str,
        variable: tk.StringVar,
        button_text: str,
        command: Callable[[], None],
        nested: bool = False,
        action_text: Optional[str] = None,
        action_command: Optional[Callable[[], None]] = None,
    ) -> tuple[ttk.Entry, Optional[ttk.Button]]:
        row = tk.Frame(parent, bg="#ffffff" if nested else "#e9eee8")
        row.pack(fill="x", pady=3)
        padx = 15 if nested else 0
        tk.Label(
            row,
            text=label,
            width=16,
            anchor="w",
            bg=row["bg"],
            fg="#52665b",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(padx, 8))
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=button_text, command=command).pack(
            side="left", padx=(8, 5 if action_text else padx)
        )
        action_button = None
        if action_text and action_command:
            action_button = ttk.Button(
                row, text=action_text, command=action_command
            )
            action_button.pack(side="left", padx=(0, padx))
        return entry, action_button

    def _browse_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择原始物编文件",
            initialdir=self._initial_dir(self.source_var.get()),
            filetypes=[
                ("Warcraft object data", "*.w3u *.w3a *.w3t *.w3q"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.source_var.set(selected)
            self.csv_var.set("")
            self.output_var.set("")
            self._refresh_format_controls()

    def _browse_data(self) -> None:
        selected = filedialog.askdirectory(
            title="选择含对应 MetaData.slk 的 mpq\\Units 文件夹",
            initialdir=self._initial_dir(self.data_var.get()),
        )
        if selected:
            self.data_var.set(selected)

    def _open_data_picker(self, _event: tk.Event) -> str:
        self._browse_data()
        return "break"

    def _save_csv_dialog(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="保存美化物编表格",
            initialdir=self._initial_dir(self.csv_var.get()),
            initialfile=Path(self.csv_var.get()).name or "物编表.xlsx",
            defaultextension=".xlsx",
            filetypes=[
                ("Excel 工作簿", "*.xlsx"),
                ("CSV", "*.csv"),
            ],
        )
        if selected:
            self.csv_var.set(selected)

    def _save_w3u_dialog(self) -> None:
        extension = self._source_extension()
        if extension not in (".w3u", ".w3a", ".w3t", ".w3q"):
            extension = ".w3u"
        labels = {
            ".w3u": "单位物编",
            ".w3a": "技能物编",
            ".w3t": "物品物编",
            ".w3q": "科技物编",
        }
        selected = filedialog.asksaveasfilename(
            title=f"保存{labels[extension]}",
            initialdir=self._initial_dir(self.output_var.get()),
            initialfile=Path(self.output_var.get()).name,
            defaultextension=extension,
            filetypes=[(f"{extension[1:].upper()} object data", f"*{extension}")],
        )
        if selected:
            self.output_var.set(selected)

    def _source_extension(self) -> str:
        source_text = self.source_var.get().strip()
        return Path(source_text).suffix.casefold() if source_text else ""

    def _refresh_format_controls(self) -> None:
        extension = self._source_extension()
        formats = {
            ".w3u": ("完整单位属性", "W3U 会合并基础单位属性与文件覆盖值。"),
            ".w3a": ("完整技能属性", "W3A 按对象和等级展开，合并技能基础属性与覆盖值。"),
            ".w3t": ("完整物品属性", "W3T 合并物品基础属性和文件覆盖值，每个物品一行。"),
            ".w3q": ("完整科技属性", "W3Q 按对象和等级展开，合并科技基础属性与覆盖值。"),
        }
        title, hint = formats.get(
            extension,
            ("物编表格", "支持 W3U、W3A、W3T、W3Q；请选择原始物编文件。"),
        )
        self.export_section.configure(text=f"02  导出{title}")
        self.export_hint.configure(text=hint)
        self.export_button.configure(text="导出表格")
        action_text = f"表格复原为 {extension[1:].upper()}" if extension else "表格复原"
        self.rebuild_button.configure(text=action_text)

    @staticmethod
    def _initial_dir(value: str) -> str:
        if not value.strip():
            return str(BASE_DIR)
        path = Path(value)
        if not path.is_absolute():
            path = BASE_DIR / path
        return str(path if path.is_dir() else path.parent)

    @staticmethod
    def _project_path(value: str) -> Path:
        path = Path(value.strip())
        return path if path.is_absolute() else BASE_DIR / path

    def _validate_paths(self) -> tuple[Path, Path]:
        source_text = self.source_var.get().strip()
        data_text = self.data_var.get().strip()
        if not source_text:
            raise core.W3UError("请先选择原始物编文件。")
        if not data_text:
            raise core.W3UError("请先选择物编基础数据目录。")
        source = self._project_path(source_text)
        data_dir = self._project_path(data_text)
        extension = source.suffix.casefold()
        metadata_files = {
            ".w3u": "UnitMetaData.slk",
            ".w3t": "UnitMetaData.slk",
            ".w3a": "AbilityMetaData.slk",
            ".w3q": "UpgradeMetaData.slk",
        }
        metadata_file = metadata_files.get(extension)
        if metadata_file is None:
            raise core.W3UError("支持的输入格式为 .w3u、.w3a、.w3t、.w3q。")
        if not source.is_file():
            raise core.W3UError(f"找不到原始物编文件：{source}")
        if not (data_dir / metadata_file).is_file():
            raise core.W3UError(f"数据目录中找不到 {metadata_file}：{data_dir}")
        return source, data_dir

    def _export_csv(self) -> None:
        self._refresh_format_controls()
        try:
            source, data_dir = self._validate_paths()
            csv_text = self.csv_var.get().strip()
            if not csv_text:
                raise core.W3UError("请指定物编表格的输出路径。")
            output = self._project_path(csv_text)
            if not output.name:
                raise core.W3UError("请指定表格输出路径。")
            if output.suffix.casefold() not in (".csv", ".xlsx"):
                raise core.W3UError("表格输出文件扩展名应为 .xlsx 或 .csv。")
            if output.resolve() == source.resolve():
                raise core.W3UError("表格输出路径不能与原始物编文件相同。")
            if output.exists() and not messagebox.askyesno(
                "确认覆盖",
                f"表格已存在，确定覆盖吗？\n{output}",
                parent=self.root,
            ):
                return
        except Exception as exc:
            messagebox.showerror("无法导出", str(exc), parent=self.root)
            return

        self._run_async(
            "正在导出完整表格…",
            lambda: self._do_export(source, data_dir, output),
        )

    @staticmethod
    def _do_export(source: Path, data_dir: Path, output: Path) -> str:
        document = core.parse_object_data(source.read_bytes(), source.suffix)
        if source.suffix.casefold() == ".w3u":
            metadata, bases, localized = core.load_unit_metadata(data_dir)
            columns, rows = core.full_export_rows(document, metadata, bases, localized)
            core.write_table(output, columns, rows, metadata, localized)
            row_count = len(rows)
            identifier_columns = 5 if document.uses_optional_ints else 4
            property_count = len(columns) - identifier_columns
            detail = f"物体 {row_count} 个，完整属性列 {property_count} 个。"
        else:
            metadata, localized = core.load_format_metadata(
                data_dir, source.suffix
            )
            bases = core.load_format_bases(data_dir, source.suffix)
            columns, rows = core.format_full_export_rows(
                document, metadata, bases, localized
            )
            core.write_table(output, columns, rows, metadata, localized)
            row_count = len(rows)
            property_count = len(columns) - 4
            detail = (
                f"物体 {sum(len(table['objects']) for table in document.tables)} 个，"
                f"等级行 {row_count} 行，完整属性列 {property_count} 个。"
            )
        return (
            f"已导出：{output}\n"
            f"{detail}\n"
            "请保留物体索引和字段索引列，只编辑字段值。"
        )

    def _rebuild(self) -> None:
        self._refresh_format_controls()
        try:
            source, data_dir = self._validate_paths()
            csv_text = self.csv_var.get().strip()
            output_text = self.output_var.get().strip()
            if not csv_text:
                raise core.W3UError("请先选择要回写的 CSV 或 XLSX 表格。")
            if not output_text:
                raise core.W3UError("请指定回写后的 W3U 输出路径。")
            csv_path = self._project_path(csv_text)
            output = self._project_path(output_text)
            if not csv_path.is_file():
                raise core.W3UError(f"找不到表格文件：{csv_path}")
            if not output.name:
                raise core.W3UError("请指定 W3U 输出路径。")
            if output.suffix.casefold() != source.suffix.casefold():
                raise core.W3UError(
                    f"输出文件扩展名必须与输入格式一致：{source.suffix}"
                )
            if output.resolve() == source.resolve():
                raise core.W3UError("输出路径不能与原始 W3U 相同。")
            if output.exists() and not messagebox.askyesno(
                "确认覆盖",
                f"输出文件已存在，确定覆盖吗？\n{output}",
                parent=self.root,
            ):
                return
        except Exception as exc:
            messagebox.showerror("无法回写", str(exc), parent=self.root)
            return

        self._run_async(
            "正在将表格复原为物编文件…",
            lambda: self._do_rebuild(source, csv_path, output, data_dir),
        )

    @staticmethod
    def _do_rebuild(
        source: Path, csv_path: Path, output: Path, data_dir: Path
    ) -> str:
        document = core.parse_object_data(source.read_bytes(), source.suffix)
        if source.suffix.casefold() == ".w3u":
            metadata, bases, _localized = core.load_unit_metadata(data_dir)
            changed = core.apply_full_csv(document, csv_path, metadata, bases)
        else:
            metadata, _localized = core.load_format_metadata(
                data_dir, source.suffix
            )
            bases = core.load_format_bases(data_dir, source.suffix)
            changed = core.apply_format_full_csv(
                document, csv_path, metadata, bases
            )
        rebuilt = core.build_object_data(core.document_to_json(document))
        core.parse_object_data(rebuilt, source.suffix)
        output.write_bytes(rebuilt)
        return (
            f"已生成：{output}\n"
            f"应用字段改动 {changed} 项；原始文件未覆盖。\n"
            "下一步：在物体编辑器选择「文件 → 导入单位设置」，导入该文件并保存地图。"
        )

    def _run_async(self, status: str, action: Callable[[], str]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_var.set(status)
        self.export_button.configure(state="disabled")
        self.rebuild_button.configure(state="disabled")
        self.progress.start(12)
        self._log(status)

        def worker() -> None:
            try:
                result = action()
                self.events.put(("success", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, message = self.events.get_nowait()
                self.busy = False
                self.progress.stop()
                self.export_button.configure(state="normal")
                self.rebuild_button.configure(state="normal")
                if kind == "success":
                    self.status_var.set("完成")
                    self._log(message)
                    messagebox.showinfo("完成", message, parent=self.root)
                else:
                    self.status_var.set("操作失败")
                    self._log(f"错误：{message}")
                    messagebox.showerror("操作失败", message, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_events)

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_guide(self) -> None:
        guide = BASE_DIR / "W3U工具使用说明.md"
        if guide.exists():
            try:
                import os

                os.startfile(guide)  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("无法打开说明", str(exc), parent=self.root)
        else:
            messagebox.showinfo("使用说明", f"说明文件不存在：\n{guide}", parent=self.root)

    def _close(self) -> None:
        if self.busy:
            messagebox.showinfo(
                "任务正在运行",
                "请等待当前导出或回写完成后再关闭面板。",
                parent=self.root,
            )
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    W3UGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
