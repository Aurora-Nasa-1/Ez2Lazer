#!/usr/bin/env python3
import subprocess
import os
import platform
import argparse
import shutil
import zipfile
import hashlib
from datetime import datetime


def run_publish(project_csproj: str, working_dir: str, config: str, out_dir: str, os_name: str, optimize: str = 'none') -> int:
    cmd = ["dotnet", "publish", project_csproj, "-c", config, "-o", out_dir, "--self-contained", "true", "--os", os_name]
    if optimize == 'v3':
        cmd.extend(["-p:OptimizedBuild=true", "-p:ReadyToRunInstructionSet=avx2"])
    print("Running:", " ".join(cmd))
    res = subprocess.run(cmd, cwd=working_dir)
    return res.returncode


def run_cleanup(script_path: str, target_dir: str, platform: str) -> int:
    # If an external script is provided and exists, run it. Otherwise use internal cleaner.
    if script_path and os.path.exists(script_path):
        print(f"Running external cleanup script: {script_path}")
        res = subprocess.run(["python", script_path, target_dir])
        return res.returncode
    else:
        print("External cleanup script not found, using internal cleanup logic")
        return clean_publish_folder(target_dir, platform)


def clean_publish_folder(release_dir=None, platform=None):
    from pathlib import Path

    if release_dir is None:
        release_dir = Path(__file__).parent / "Release"
    else:
        release_dir = Path(release_dir)

    if not release_dir.exists():
        print(f"Release folder does not exist: {release_dir}")
        return 0

    print(f"Starting cleanup of folder: {release_dir}")

    deleted_files = 0
    deleted_folders = 0

    # 1. 删除 .pdb 文件
    for pdb_file in release_dir.rglob("*.pdb"):
        try:
            pdb_file.unlink()
            print(f"Deleted PDB file: {pdb_file.name}")
            deleted_files += 1
        except Exception as e:
            print(f"Failed to delete {pdb_file}: {e}")

    # 2. 删除调试和诊断相关的XML文件（而不是所有XML文件）
    debug_xml_patterns = [
        "*Microsoft*.xml", "*System*.xml", "*osu*.xml", "*Veldrid*.xml", "*MongoDB*.xml", "*Newtonsoft*.xml", "*TagLib*.xml", "*HtmlAgilityPack*.xml", "*DiscordRPC*.xml", "*FFmpeg*.xml", "*Sentry*.xml", "*Realm*.xml", "*NuGet*.xml"
    ]
    for pattern in debug_xml_patterns:
        for xml_file in release_dir.rglob(pattern):
            try:
                xml_file.unlink()
                print(f"Deleted documentation file: {xml_file.name}")
                deleted_files += 1
            except Exception as e:
                print(f"Failed to delete {xml_file}: {e}")

    # 清理 runtime 文件夹
    runtime_dir = release_dir / "runtimes"
    if runtime_dir.exists():
        print(f"Processing runtime folder: {runtime_dir}")
        # choose keep list based on platform if provided
        if platform is None:
            keep_runtimes = {"win-x64", "win-x86"}
        else:
            if platform == 'windows':
                keep_runtimes = {"win-x64", "win-x86"}
            elif platform == 'linux':
                keep_runtimes = {"linux-x64"}
            elif platform == 'macos':
                keep_runtimes = {"osx-x64", "osx-arm64"}
            else:
                keep_runtimes = {"win-x64", "win-x86"}

        for runtime_folder in runtime_dir.iterdir():
            if runtime_folder.is_dir():
                runtime_name = runtime_folder.name
                if runtime_name not in keep_runtimes:
                    try:
                        shutil.rmtree(runtime_folder)
                        print(f"Deleted runtime folder: {runtime_name}")
                        deleted_folders += 1
                    except Exception as e:
                        print(f"Failed to delete runtime folder {runtime_name}: {e}")
                else:
                    print(f"Keeping runtime folder: {runtime_name}")

    print(f"\nCleanup complete!")
    print(f"Deleted files: {deleted_files}")
    print(f"Deleted folders: {deleted_folders}")
    return 0


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def zip_folder(src_dir: str, zip_path: str):
    """Create a deterministic zip of src_dir at zip_path."""
    if os.path.exists(zip_path):
        os.remove(zip_path)

    FIXED_DATETIME = (1980, 1, 1, 0, 0, 0)

    def _iter_files(root_dir):
        for root, dirs, files in os.walk(root_dir):
            dirs.sort()
            files.sort()
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, root_dir)
                arcname = rel.replace(os.path.sep, '/')
                yield full, arcname

    compression = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(zip_path, 'w', compression=compression) as zf:
        for full, arcname in _iter_files(src_dir):
            zi = zipfile.ZipInfo(arcname)
            zi.date_time = FIXED_DATETIME
            zi.external_attr = 0o644 << 16
            with open(full, 'rb') as fh:
                data = fh.read()
            zf.writestr(zi, data, compress_type=compression)

    try:
        size = os.path.getsize(zip_path)
        sha256 = _compute_sha256(zip_path)
        print(f"Created zip: {zip_path}")
        print(f"ZIP size: {size} bytes")
        print(f"ZIP SHA256: {sha256}")
    except Exception as e:
        print(f"Created zip but failed to compute diagnostics: {e}")


def create_appimage(source_dir: str, output_path: str, workdir: str):
    print(f"Creating AppImage from {source_dir} to {output_path}")
    appdir = os.path.join(workdir, "AppDir")
    if os.path.exists(appdir):
        shutil.rmtree(appdir)
    os.makedirs(appdir, exist_ok=True)

    # Copy files and rename main executable to ez2lazer for consistency
    main_exe = "Ez2osu!"
    for item in os.listdir(source_dir):
        s = os.path.join(source_dir, item)
        d = os.path.join(appdir, "ez2lazer" if item == main_exe else item)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    # Create AppRun
    apprun_content = """#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}:${PATH}"
export LD_LIBRARY_PATH="${HERE}:${LD_LIBRARY_PATH}"
cd "${HERE}"
exec ./ez2lazer "$@"
"""
    with open(os.path.join(appdir, "AppRun"), "w") as f:
        f.write(apprun_content)
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)

    # Create .desktop file
    desktop_content = """[Desktop Entry]
Type=Application
Name=Ez2Lazer
Comment=A free-to-win rhythm game. Rhythm is just a *click* away!
Exec=ez2lazer
Icon=ez2lazer
Terminal=false
Categories=Game;
"""
    with open(os.path.join(appdir, "ez2lazer.desktop"), "w") as f:
        f.write(desktop_content)

    # Copy Icon
    icon_src = os.path.join(workdir, "assets", "lazer.png")
    if os.path.exists(icon_src):
        shutil.copy2(icon_src, os.path.join(appdir, "ez2lazer.png"))
    else:
        print("Warning: assets/lazer.png not found, AppImage might not have an icon")

    # Download appimagetool if not present
    appimagetool_path = os.path.join(workdir, "appimagetool-x86_64.AppImage")
    if not os.path.exists(appimagetool_path):
        print("Downloading appimagetool...")
        # Use the newer appimagetool repository and a stable version
        url = "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
        # Use -f to fail on HTTP errors
        subprocess.run(["curl", "-Lf", "-o", appimagetool_path, url], check=True)
        os.chmod(appimagetool_path, 0o755)

    # Run appimagetool
    env = os.environ.copy()
    env["ARCH"] = "x86_64"
    # Use --appimage-extract-and-run to avoid FUSE issues in CI
    cmd = [appimagetool_path, "--appimage-extract-and-run", appdir, output_path]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description="Publish and package Ez2Lazer builds.")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    gh_workspace = os.environ.get('GITHUB_WORKSPACE', script_dir)
    parser.add_argument('--project', default=os.path.join(gh_workspace, 'osu.Desktop', 'osu.Desktop.csproj'))
    parser.add_argument('--workdir', default=gh_workspace)
    parser.add_argument('--cleanup-release', default=None)
    parser.add_argument('--cleanup-debug', default=None)
    parser.add_argument('--outroot', default=gh_workspace)
    parser.add_argument('--zip-only', action='store_true', help='Only create zip files')
    parser.add_argument('--no-zip', action='store_true', help='Do not create zip files')
    parser.add_argument('--pack-appimage', action='store_true', help='Pack AppImage')
    parser.add_argument('--optimize', choices=['none', 'v3'], default='none', help='Optimization level')
    parser.add_argument('--tag', default=None, help='Optional tag to include in asset name')
    parser.add_argument('--deps-path', default=None, help='Path to folder containing dependency DLLs')
    parser.add_argument('--deps-pattern', default='*.dll', help='Glob pattern for dependency files')
    parser.add_argument('--deps-source', choices=['local','github','none'], default='local', help='Where to get dependency DLLs')
    parser.add_argument('--deps-github-repo', default='SK-la/osu-framework', help='GitHub repo for dependencies')
    parser.add_argument('--deps-github-branch', default='locmain', help='Branch for dependencies')
    parser.add_argument('--deps-github-project', default='osu.Framework/osu.Framework.csproj', help='Project path in deps repo')
    parser.add_argument('--resources-github-repo', default='SK-la/osu-resources', help='GitHub repo for resources')
    parser.add_argument('--resources-github-path', default='osu.Game.Resources/Resources', help='Path inside resources repo')
    parser.add_argument('--resources-path', default=None, help='Local path to resources')
    parser.add_argument('--platform', default=None, help='Platform name')
    args = parser.parse_args()

    if not args.tag:
        today = datetime.utcnow()
        args.tag = f"{today.year}-{today.month}-{today.day}"
        print(f"No --tag provided; defaulting to {args.tag}")

    tag_suffix = f"_{args.tag}"
    base_out = args.outroot
    release_dir = os.path.join(base_out, 'Ez2Lazer_release_x64')
    debug_dir = os.path.join(base_out, 'Ez2Lazer_debug_x64')

    for d in (release_dir, debug_dir):
        if os.path.exists(d):
            shutil.rmtree(d)

    target_platform = args.platform or platform.system().lower()
    print("Building for platform", target_platform)

    print('Publishing Release...')
    rc = run_publish(args.project, args.workdir, 'Release', release_dir, target_platform, args.optimize)
    if rc != 0:
        print('Release publish failed')
    else:
        run_cleanup(args.cleanup_release, release_dir, target_platform)

    print('Publishing Debug...')
    rc2 = run_publish(args.project, args.workdir, 'Debug', debug_dir, target_platform, args.optimize)
    if rc2 != 0:
        print('Debug publish failed')
    else:
        run_cleanup(args.cleanup_debug, debug_dir, target_platform)

    artifacts_dir = os.path.join(base_out, 'artifacts')
    try:
        os.makedirs(artifacts_dir, exist_ok=True)
    except PermissionError:
        fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'artifacts')
        print(f"Permission denied creating {artifacts_dir}, falling back to {fallback}")
        os.makedirs(fallback, exist_ok=True)
        artifacts_dir = fallback

    opt_suffix = "_v3" if args.optimize == 'v3' else ""
    release_zip = os.path.join(artifacts_dir, f"Ez2Lazer_release_{target_platform}_x64{opt_suffix}{tag_suffix}.zip")
    debug_zip = os.path.join(artifacts_dir, f"Ez2Lazer_debug_{target_platform}_x64{opt_suffix}{tag_suffix}.zip")

    if os.path.exists(release_dir):
        temp_dirs = []
        try:
            if args.deps_source == 'github':
                import tempfile
                tmp = tempfile.mkdtemp(prefix='deps-')
                temp_dirs.append(tmp)
                print(f"Cloning {args.deps_github_repo}@{args.deps_github_branch} into {tmp}")
                subprocess.run(["git","clone","--depth","1","--branch",args.deps_github_branch,f"https://github.com/{args.deps_github_repo}.git", tmp], check=True)

                # Candidate project path if provided
                candidate = os.path.join(tmp, *args.deps_github_project.split('/'))
                proj_to_build = None
                if os.path.exists(candidate):
                    proj_to_build = candidate
                else:
                    csproj_matches = []
                    for root, dirs, files in os.walk(tmp):
                        for f in files:
                            if f.endswith('.csproj'):
                                csproj_matches.append(os.path.join(root, f))
                    if csproj_matches:
                        preferred = None
                        for p in csproj_matches:
                            if 'osu.Framework' in os.path.basename(p) or 'osu.Framework' in p:
                                preferred = p
                                break
                        proj_to_build = preferred or csproj_matches[0]

                if proj_to_build:
                    print('Building dependency project', proj_to_build)
                    subprocess.run(["dotnet","build",proj_to_build,"-c","Release","-f","net8.0"], check=True)
                    deps_src_path = os.path.join(os.path.dirname(proj_to_build), 'bin', 'Release', 'net8.0')
                    import glob
                    for f in glob.glob(os.path.join(deps_src_path, args.deps_pattern)):
                        shutil.copy(f, release_dir)

            if args.deps_source == 'github' and args.resources_github_repo:
                import tempfile
                tmpres = tempfile.mkdtemp(prefix='res-')
                temp_dirs.append(tmpres)
                subprocess.run(["git","clone","--depth","1","--branch",args.deps_github_branch,f"https://github.com/{args.resources_github_repo}.git", tmpres], check=True)
                srcres = os.path.join(tmpres, args.resources_github_path.replace('/','\\' if os.name=='nt' else '/'))
                if os.path.exists(srcres):
                    destres = os.path.join(release_dir, 'Resources')
                    shutil.rmtree(destres, ignore_errors=True)
                    shutil.copytree(srcres, destres)

            if args.resources_path and os.path.exists(args.resources_path):
                destres = os.path.join(release_dir, 'Resources')
                shutil.rmtree(destres, ignore_errors=True)
                shutil.copytree(args.resources_path, destres)

        finally:
            for d in temp_dirs:
                shutil.rmtree(d, ignore_errors=True)

        if not args.no_zip:
            print('Zipping release ->', release_zip)
            zip_folder(release_dir, release_zip)

        if args.pack_appimage and target_platform == 'linux':
            appimage_path = os.path.join(artifacts_dir, f"Ez2Lazer_{args.tag}{opt_suffix}-x86_64.AppImage")
            create_appimage(release_dir, appimage_path, args.workdir)

    if os.path.exists(debug_dir) and not args.no_zip:
        print('Zipping debug ->', debug_zip)
        zip_folder(debug_dir, debug_zip)

    print('Done.')


if __name__ == '__main__':
    main()
