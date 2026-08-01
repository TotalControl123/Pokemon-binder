#!/usr/bin/env python3
"""
Pokemon binder - a window instead of a command line.

Launch it with Binder.bat (Windows) or: python binder_gui.py

Everything it does is the same as the scripts: copy the scraper, collect the
newest export, build index.html, commit and push. Nothing here needs
PowerShell.
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    sys.exit(
        "Tkinter is missing. Reinstall Python from python.org and make sure\n"
        "'tcl/tk and IDLE' is ticked in the installer."
    )

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "binder-gui.json"
STATE = HERE / ".binder-state.json"

BG = "#14161d"
PANEL = "#1b2030"
EDGE = "#2b3247"
FG = "#e9eaf2"
MUTE = "#79809a"
ACCENT = "#67e8f9"
WARN = "#fcd34d"
BAD = "#f87171"
OK = "#86efac"


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        CONFIG.write_text(json.dumps(cfg, indent=1))
    except OSError:
        pass


def newest_export(folder):
    """Newest collectr-export*.csv by modified time, not by filename.

    Windows names repeat downloads 'collectr-export (2).csv', so the plain
    name is often the oldest one there.
    """
    try:
        files = [
            p for p in Path(folder).glob("collectr-export*.csv") if p.is_file()
        ]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def looks_like_export(path):
    """Cheap guard against building from some unrelated CSV."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            head = fh.readline().lower()
    except OSError:
        return False
    return all(col in head for col in ("name", "set", "qty"))


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.q = queue.Queue()
        self.busy = False
        # Tk widgets may only be touched from the main thread, so worker
        # threads read this snapshot rather than the entry boxes themselves.
        self.snap = {}

        root.title("Pokemon binder")
        root.configure(bg=BG)
        root.geometry("760x620")
        root.minsize(660, 520)

        self._build_ui()
        self._refresh_status()
        self.root.after(80, self._drain)

    # ---------------------------------------------------------------- layout
    def _build_ui(self):
        pad = {"padx": 16, "pady": 6}

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", pady=(14, 0))
        tk.Label(
            head, text="Pokemon binder", bg=BG, fg=FG,
            font=("Segoe UI Semibold", 19),
        ).pack(side="left", padx=16)
        self.status = tk.Label(head, text="", bg=BG, fg=MUTE, font=("Consolas", 9))
        self.status.pack(side="right", padx=16)

        # --- settings
        box = tk.LabelFrame(
            self.root, text=" Settings ", bg=BG, fg=MUTE,
            font=("Segoe UI", 9), bd=1, relief="solid",
            highlightbackground=EDGE,
        )
        box.pack(fill="x", **pad)

        self.owner = self._field(box, "Your name", self.cfg.get("owner", ""), 0)
        self.url = self._field(
            box, "Showcase link", self.cfg.get("showcase", ""), 1
        )
        self.dl = self._field(
            box, "Downloads folder",
            self.cfg.get("downloads", str(Path.home() / "Downloads")), 2,
            browse=True,
        )

        # --- steps
        steps = tk.Frame(self.root, bg=BG)
        steps.pack(fill="x", **pad)

        self.b1 = self._button(
            steps, "1.  Copy scraper && open my showcase", self.step_scrape, ACCENT
        )
        self.b1.pack(fill="x", pady=(0, 6))
        tk.Label(
            steps,
            text="Then in the browser: press F12, click Console, paste, "
                 "press Enter, and run   await collectrExport()",
            bg=BG, fg=MUTE, font=("Segoe UI", 9), justify="left", wraplength=700,
        ).pack(anchor="w", pady=(0, 10))

        self.b2 = self._button(
            steps, "2.  Update my binder and publish it", self.step_publish, OK
        )
        self.b2.pack(fill="x")

        opts = tk.Frame(self.root, bg=BG)
        opts.pack(fill="x", padx=16)
        self.force = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts, text="I really did sell that many (skip the size check)",
            variable=self.force, bg=BG, fg=MUTE, selectcolor=PANEL,
            activebackground=BG, activeforeground=FG, font=("Segoe UI", 9),
            bd=0, highlightthickness=0,
        ).pack(side="left")
        tk.Button(
            opts, text="Open the live page", command=self.open_live,
            bg=BG, fg=MUTE, bd=0, font=("Segoe UI", 9), cursor="hand2",
            activebackground=BG, activeforeground=FG,
        ).pack(side="right")

        # --- log
        wrap = tk.Frame(self.root, bg=EDGE, bd=0)
        wrap.pack(fill="both", expand=True, padx=16, pady=(10, 16))
        self.log = tk.Text(
            wrap, bg=PANEL, fg=FG, insertbackground=FG, bd=0,
            font=("Consolas", 9), wrap="word", padx=10, pady=8,
        )
        self.log.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        bar = ttk.Scrollbar(wrap, command=self.log.yview)
        bar.pack(side="right", fill="y")
        self.log.config(yscrollcommand=bar.set, state="disabled")
        for tag, colour in (("err", BAD), ("warn", WARN), ("ok", OK), ("dim", MUTE)):
            self.log.tag_config(tag, foreground=colour)

    def _field(self, parent, label, value, row, browse=False):
        tk.Label(
            parent, text=label, bg=BG, fg=MUTE, font=("Segoe UI", 9)
        ).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=5)
        var = tk.StringVar(value=value)
        e = tk.Entry(
            parent, textvariable=var, bg=PANEL, fg=FG, insertbackground=FG,
            bd=0, font=("Segoe UI", 10), highlightthickness=1,
            highlightbackground=EDGE, highlightcolor=ACCENT,
        )
        e.grid(row=row, column=1, sticky="ew", pady=5, ipady=4)
        parent.columnconfigure(1, weight=1)
        if browse:
            tk.Button(
                parent, text="...", command=lambda: self._pick(var),
                bg=PANEL, fg=FG, bd=0, font=("Segoe UI", 9), cursor="hand2",
            ).grid(row=row, column=2, padx=(6, 12))
        else:
            tk.Label(parent, text="", bg=BG).grid(row=row, column=2, padx=(6, 12))
        return var

    def _pick(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)

    def _button(self, parent, text, cmd, colour):
        return tk.Button(
            parent, text=text, command=cmd, bg=colour, fg="#0d1018",
            activebackground=colour, activeforeground="#0d1018",
            bd=0, font=("Segoe UI Semibold", 11), cursor="hand2", pady=10,
        )

    # ----------------------------------------------------------------- utils
    def say(self, text, tag=None):
        self.q.put(("log", text, tag))

    def _drain(self):
        """Pump worker-thread messages onto the UI thread."""
        while True:
            try:
                kind, a, b = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log.config(state="normal")
                self.log.insert("end", a + "\n", b or ())
                self.log.see("end")
                self.log.config(state="disabled")
            elif kind == "done":
                self.busy = False
                self.b1.config(state="normal")
                self.b2.config(state="normal")
                self._refresh_status()
        self.root.after(80, self._drain)

    def _refresh_status(self):
        try:
            st = json.loads(STATE.read_text())
            self.status.config(
                text=f"{st.get('items','?')} cards  ·  last built {st.get('date','?')}"
            )
        except (OSError, ValueError):
            self.status.config(text="not built yet")

    def _save(self):
        """Read the entry boxes and stash them. Main thread only."""
        self.cfg.update(
            owner=self.owner.get().strip(),
            showcase=self.url.get().strip(),
            downloads=self.dl.get().strip(),
        )
        self.snap = {
            "owner": self.cfg["owner"],
            "showcase": self.cfg["showcase"],
            "downloads": self.cfg["downloads"],
            "force": bool(self.force.get()),
        }
        save_config(self.cfg)

    def run(self, fn):
        if self.busy:
            return
        self.busy = True
        self.b1.config(state="disabled")
        self.b2.config(state="disabled")
        self._save()
        threading.Thread(target=self._wrap, args=(fn,), daemon=True).start()

    def _wrap(self, fn):
        try:
            fn()
        except Exception as e:                                   # noqa: BLE001
            self.say(f"Something went wrong: {e}", "err")
        finally:
            self.q.put(("done", None, None))

    def shell(self, args, label=None):
        """Run a command, streaming its output into the log."""
        if label:
            self.say(label, "dim")
        try:
            p = subprocess.Popen(
                args, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            self.say(f"Couldn't run {args[0]} - is it installed?", "err")
            return 1
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            tag = "err" if line.lstrip().startswith("!") or "refus" in line else None
            self.say("   " + line, tag)
        return p.wait()

    # ----------------------------------------------------------------- steps
    def step_scrape(self):
        url = self.url.get().strip()
        script = HERE / "collectr-export-v2.js"
        if not script.exists():
            messagebox.showerror(
                "Missing file",
                "collectr-export-v2.js isn't in this folder, so there's "
                "nothing to copy.",
            )
            return
        self._save()
        self.root.clipboard_clear()
        self.root.clipboard_append(script.read_text(encoding="utf-8"))
        self.say("Scraper copied to your clipboard.", "ok")
        if url:
            import webbrowser

            webbrowser.open(url)
            self.say("Opened your showcase page.", "dim")
        else:
            self.say("No showcase link set - open your page yourself.", "warn")
        self.say("In the browser: F12 -> Console -> paste -> Enter, then run:", "dim")
        self.say("   await collectrExport()", "dim")
        self.say("When the CSV has downloaded, click step 2.", "dim")

    def step_publish(self):
        self.run(self._publish)

    def _publish(self):
        target = HERE / "collectr-export.csv"

        found = newest_export(self.snap.get("downloads", ""))
        if found:
            mine = target.stat().st_mtime if target.exists() else 0
            if found.stat().st_mtime > mine:
                self.say(f"Found {found.name} in Downloads.", "ok")
                shutil.move(str(found), str(target))
            else:
                self.say("Nothing newer in Downloads - using what's here.", "warn")
        elif target.exists():
            self.say("No export in Downloads - using what's here.", "warn")
        else:
            self.say("No export anywhere. Do step 1 first.", "err")
            return

        if not looks_like_export(target):
            self.say("That file doesn't look like a CollectR export.", "err")
            return

        rows = sum(1 for _ in open(target, encoding="utf-8-sig")) - 1
        self.say(f"{rows} rows to process.", "dim")

        cmd = [sys.executable, "build_binder.py", str(target), "-o", "index.html"]
        if self.snap.get("owner"):
            cmd += ["--owner", self.snap["owner"]]
        if self.snap.get("force"):
            cmd += ["--force"]

        self.say("")
        if self.shell(cmd, "Building your binder...") != 0:
            self.say("")
            self.say(
                "Nothing was published. If the scrape stopped early, run step 1 "
                "again and let it finish scrolling. If you really did sell that "
                "many cards, tick the box above and retry.",
                "warn",
            )
            return

        self.say("")
        if not (HERE / ".git").exists():
            self.say("Built index.html. No git repo here, so nothing to push.", "ok")
            return

        self.shell(["git", "add", "-A"], "Publishing...")
        if subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=HERE
        ).returncode == 0:
            self.say("Nothing changed since last time.", "warn")
            return
        import datetime

        self.shell(
            ["git", "commit", "-q", "-m",
             f"binder update {datetime.date.today().isoformat()}"]
        )
        if self.shell(["git", "push", "-q"]) != 0:
            self.say("Push failed - check your internet or GitHub login.", "err")
            return

        self.say("")
        self.say("Published. Live in a minute or two.", "ok")
        live = self.live_url()
        if live:
            self.cfg["live"] = live
            save_config(self.cfg)
            self.say(live, "ok")

    def live_url(self):
        try:
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"], cwd=HERE,
                capture_output=True, text=True,
            ).stdout.strip()
        except OSError:
            return None
        m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", remote)
        return f"https://{m.group(1).lower()}.github.io/{m.group(2)}/" if m else None

    def open_live(self):
        url = self.cfg.get("live") or self.live_url()
        if url:
            import webbrowser

            webbrowser.open(url)
        else:
            messagebox.showinfo(
                "Not published yet",
                "Publish once and the live link will work from here.",
            )


def main():
    if not (HERE / "build_binder.py").exists():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Wrong folder",
            "build_binder.py isn't next to this file.\n\n"
            "Put binder_gui.py in your pokemon-binder folder.",
        )
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
