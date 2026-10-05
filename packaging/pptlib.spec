from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).parent
package_root = project_root / "src" / "pptlib"
datas = [
    (str(package_root / "default.toml"), "pptlib"),
    (str(package_root / "migrations"), "pptlib/migrations"),
    (str(package_root / "web" / "templates"), "pptlib/web/templates"),
    (str(package_root / "web" / "static"), "pptlib/web/static"),
]
datas += collect_data_files("playwright")

a = Analysis(
    [str(project_root / "packaging" / "pptlib_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("uvicorn"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pptlib",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pptlib",
)
