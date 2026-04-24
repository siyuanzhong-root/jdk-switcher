import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from env_manager import (
    get_current_java_home,
    get_effective_java_runtime,
    is_admin,
    switch_jdk,
)
from jdk_scanner import get_java_version, scan_jdks


class JdkSwitcherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JDK 一键切换工具")
        self.geometry("820x520")
        self.minsize(820, 520)
        self.jdk_list: list[dict] = []
        self._build_ui()
        self._refresh_current()

    def _build_ui(self) -> None:
        top = tk.Frame(self, padx=12, pady=10)
        top.pack(fill=tk.X)

        tk.Label(top, text="当前 JAVA_HOME：", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.lbl_current_home = tk.Label(
            top,
            text="",
            fg="#1a6e1a",
            font=("Consolas", 10),
            anchor="w",
            justify="left",
            wraplength=650,
        )
        self.lbl_current_home.grid(row=0, column=1, sticky="w")

        tk.Label(top, text="实际生效 java：", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=1, column=0, sticky="nw", pady=(8, 0)
        )
        self.lbl_runtime = tk.Label(
            top,
            text="",
            fg="#0b4f8a",
            font=("Consolas", 10),
            anchor="w",
            justify="left",
            wraplength=650,
        )
        self.lbl_runtime.grid(row=1, column=1, sticky="w", pady=(8, 0))

        admin_text = "管理员模式" if is_admin() else "普通用户模式"
        tk.Label(top, text=admin_text, fg="gray", font=("Microsoft YaHei UI", 9)).grid(
            row=0, column=2, rowspan=2, sticky="e", padx=(16, 0)
        )
        top.grid_columnconfigure(1, weight=1)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        mid = tk.Frame(self, padx=12, pady=8)
        mid.pack(fill=tk.BOTH, expand=True)

        tk.Label(mid, text="已发现的 JDK：", font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W)

        list_frame = tk.Frame(mid)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            font=("Consolas", 10),
            selectmode=tk.SINGLE,
            activestyle="dotbox",
        )
        scrollbar.config(command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.listbox.bind("<Double-Button-1>", lambda _event: self._switch())

        self.lbl_status = tk.Label(self, text="", fg="gray", font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(anchor=tk.W, padx=12)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        tip_text = (
            "说明：切换后，新的终端/IDE 进程会使用新 JDK；已经打开的终端不会自动替换自己的环境变量。"
        )
        tk.Label(
            self,
            text=tip_text,
            fg="#8a5a00",
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=8,
            justify="left",
            anchor="w",
        ).pack(fill=tk.X)

        btn_frame = tk.Frame(self, padx=12, pady=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="扫描 D 盘", command=self._start_scan, width=14).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="手动添加", command=self._add_manual, width=14).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_frame, text="切换所选 JDK", command=self._switch, width=16).pack(
            side=tk.RIGHT, padx=4
        )

    def _refresh_current(self) -> None:
        current_home = get_current_java_home()
        runtime = get_effective_java_runtime()

        self.lbl_current_home.config(text=current_home if current_home else "未设置")

        runtime_lines = [
            runtime.get("java_path") or "(未在有效 PATH 中找到 java.exe)",
            runtime.get("version") or "未知版本",
        ]
        self.lbl_runtime.config(text="\n".join(runtime_lines))

    def _start_scan(self) -> None:
        self.listbox.delete(0, tk.END)
        self.jdk_list.clear()
        self.lbl_status.config(text="正在扫描 D 盘，请稍候...")
        self.update_idletasks()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self) -> None:
        results = scan_jdks("D:\\")
        self.after(0, self._on_scan_done, results)

    def _on_scan_done(self, results: list[dict]) -> None:
        self.jdk_list = results
        self.listbox.delete(0, tk.END)
        current_home = os.path.normcase(get_current_java_home())

        if results:
            for item in results:
                label = f"{item['path']}  |  {item['version']}"
                self.listbox.insert(tk.END, label)
                if os.path.normcase(item["path"]) == current_home:
                    index = self.listbox.size() - 1
                    self.listbox.itemconfig(index, fg="#1a6e1a", selectforeground="#1a6e1a")
            self.lbl_status.config(text=f"共发现 {len(results)} 个 JDK")
        else:
            self.lbl_status.config(text="没有在 D 盘找到 JDK，可以手动添加")

    def _add_manual(self) -> None:
        path = filedialog.askdirectory(title="选择 JDK 根目录")
        if not path:
            return

        path = path.replace("/", "\\")
        if not os.path.exists(os.path.join(path, "bin", "java.exe")):
            messagebox.showerror("错误", f"所选目录不是有效的 JDK 根目录：\n{path}")
            return

        version = get_java_version(os.path.join(path, "bin", "java.exe"))
        item = {"path": path, "version": version}
        if any(os.path.normcase(existing["path"]) == os.path.normcase(path) for existing in self.jdk_list):
            self.lbl_status.config(text=f"该 JDK 已存在：{path}")
            return

        self.jdk_list.append(item)
        self.listbox.insert(tk.END, f"{path}  |  {version}")
        self.lbl_status.config(text=f"已手动添加：{path}")

    def _switch(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个 JDK")
            return

        item = self.jdk_list[selection[0]]
        ok, msg = switch_jdk(item["path"])
        self._refresh_current()
        self._on_scan_done(self.jdk_list)

        if ok:
            messagebox.showinfo("切换成功", msg)
            self.lbl_status.config(text=f"已切换到：{item['path']}")
        else:
            messagebox.showerror("切换失败", msg)


if __name__ == "__main__":
    app = JdkSwitcherApp()
    app.mainloop()
