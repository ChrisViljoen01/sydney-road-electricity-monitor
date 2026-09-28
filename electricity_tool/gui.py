from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import DEFAULT_BASE_URL, DEFAULT_EXPORT_DIR, load_accounts
from .credentials import CredentialError, WindowsCredentialStore
from .service import run_extraction


class ElectricityToolApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Electricity Reading Tool")
        self.root.geometry("820x690")
        self.root.minsize(720, 600)
        self.store: WindowsCredentialStore | None = None
        try:
            self.store = WindowsCredentialStore()
        except CredentialError:
            pass

        today = date.today()
        self.base_url = tk.StringVar(value=DEFAULT_BASE_URL)
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.start_date = tk.StringVar(value=today.replace(month=1, day=1).isoformat())
        self.end_date = tk.StringVar(value=today.isoformat())
        self.output_dir = tk.StringVar(value=str(DEFAULT_EXPORT_DIR))
        self.save_login = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self._build()
        self._load_saved_login()

    def _build(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="PNPSCADA Electricity Extraction", font=("Segoe UI", 16, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            container,
            text="Area monitoring • daily/weekly spikes • kW, kVA, kWh • CSV output",
        ).pack(anchor="w", pady=(2, 14))

        form = ttk.Frame(container)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        labels = ["Portal URL", "Username", "Password", "Start date", "End date", "Output folder"]
        for row, label in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(form, textvariable=self.base_url).grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Entry(form, textvariable=self.username).grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Entry(form, textvariable=self.password, show="•").grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=5
        )
        ttk.Entry(form, textvariable=self.start_date, width=18).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Label(form, text="YYYY-MM-DD").grid(row=3, column=2, sticky="w", padx=(8, 0))
        ttk.Entry(form, textvariable=self.end_date, width=18).grid(row=4, column=1, sticky="w", pady=5)
        ttk.Label(form, text="inclusive").grid(row=4, column=2, sticky="w", padx=(8, 0))
        ttk.Entry(form, textvariable=self.output_dir).grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Button(form, text="Browse...", command=self._browse).grid(row=5, column=2, padx=(8, 0), pady=5)

        quick = ttk.Frame(container)
        quick.pack(fill="x", pady=(8, 3))
        ttk.Label(quick, text="Quick range:").pack(side="left")
        ttk.Button(quick, text="Last 7 days", command=lambda: self._set_range("week")).pack(
            side="left", padx=(8, 4)
        )
        ttk.Button(quick, text="Month to date", command=lambda: self._set_range("mtd")).pack(
            side="left", padx=4
        )
        ttk.Button(quick, text="Year to date", command=lambda: self._set_range("ytd")).pack(
            side="left", padx=4
        )

        credential_row = ttk.Frame(container)
        credential_row.pack(fill="x", pady=(4, 12))
        ttk.Checkbutton(
            credential_row,
            text="Save login in Windows Credential Manager",
            variable=self.save_login,
        ).pack(side="left")
        ttk.Button(credential_row, text="Forget saved login", command=self._forget_login).pack(side="right")

        accounts = load_accounts()
        account_frame = ttk.LabelFrame(container, text=f"Configured meter accounts ({len(accounts)})", padding=8)
        account_frame.pack(fill="x", pady=(0, 12))
        for account in accounts:
            ttk.Label(account_frame, text=f"• {account.name} — {account.code} ({account.eid})").pack(anchor="w")

        actions = ttk.Frame(container)
        actions.pack(fill="x")
        self.run_button = ttk.Button(actions, text="Extract to CSV", command=self._start)
        self.run_button.pack(side="left")
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=12)

        self.log = tk.Text(container, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(10, 0))

    def _browse(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(path)

    def _set_range(self, kind: str) -> None:
        today = date.today()
        if kind == "week":
            start = today - timedelta(days=6)
        elif kind == "mtd":
            start = today.replace(day=1)
        else:
            start = today.replace(month=1, day=1)
        self.start_date.set(start.isoformat())
        self.end_date.set(today.isoformat())

    def _append_log(self, message: str) -> None:
        def update() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", message.rstrip() + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        self.root.after(0, update)

    def _load_saved_login(self) -> None:
        if self.store is None:
            return
        try:
            saved = self.store.read()
        except CredentialError as exc:
            self._append_log(str(exc))
            return
        if saved:
            self.username.set(saved[0])
            self.password.set(saved[1])
            self.save_login.set(True)
            self.status.set("Saved Windows login loaded")

    def _forget_login(self) -> None:
        if self.store is None:
            messagebox.showinfo("Credential Manager", "Windows Credential Manager is unavailable.")
            return
        try:
            self.store.delete()
            self.password.set("")
            self.save_login.set(False)
            self.status.set("Saved login removed")
        except CredentialError as exc:
            messagebox.showerror("Credential Manager", str(exc))

    def _start(self) -> None:
        try:
            start = date.fromisoformat(self.start_date.get().strip())
            end = date.fromisoformat(self.end_date.get().strip())
        except ValueError:
            messagebox.showerror("Dates", "Enter dates in YYYY-MM-DD format.")
            return
        username = self.username.get().strip()
        password = self.password.get()
        base_url = self.base_url.get().strip()
        output_dir = Path(self.output_dir.get())
        save_login = self.save_login.get()
        if not username or not password:
            messagebox.showerror("Login", "Enter the existing PNPSCADA username and password.")
            return
        if end < start:
            messagebox.showerror("Dates", "End date cannot be before start date.")
            return

        self.run_button.configure(state="disabled")
        self.status.set("Running...")
        self._append_log(f"Starting extraction for {start} through {end}...")

        def worker() -> None:
            try:
                result = run_extraction(
                    base_url,
                    username,
                    password,
                    start,
                    end,
                    output_dir,
                    progress=self._append_log,
                    on_login_success=(
                        (lambda: self.store.write(username, password))
                        if save_login and self.store is not None
                        else None
                    ),
                )
                self.root.after(0, lambda: self._success(result))
            except Exception as exc:
                self._append_log(f"ERROR: {exc}")
                self.root.after(0, lambda: self._failure(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _success(self, path: Path) -> None:
        self.run_button.configure(state="normal")
        self.status.set("Completed")
        messagebox.showinfo("Extraction complete", f"CSV files were created in:\n\n{path}")

    def _failure(self, message: str) -> None:
        self.run_button.configure(state="normal")
        self.status.set("Failed")
        messagebox.showerror("Extraction failed", message)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    ElectricityToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
