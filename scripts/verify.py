"""Exercise the packaged launchers on an isolated test SDK."""

from hashlib import sha1
import os
from pathlib import Path
from shutil import move
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from build import unpack_tar
from inputs import CACHE, LOCK_PATH, OUT, ROOT, fetch, read_lock

TESTS = ROOT / "tests"


def verify_manager(bin_dir: Path, sdk: Path, work: Path, version: str) -> None:
    manager = bin_dir / "sdkmanager"
    assert run(manager, "--version").stdout.strip() == version
    run(bin_dir / "avdmanager", "list", "avd")
    # An empty SDK can list its packages without requiring a repository.
    run(manager, "--list_installed")

    repo = work / "repository"
    repo.mkdir()
    package = repo / "fixture.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr("fixture/source.properties", "Pkg.Revision=1\n")
        archive.writestr("fixture/payload.txt", "offline installation\n")
    digest = sha1(package.read_bytes(), usedforsecurity=False).hexdigest()
    index = (TESTS / "repository.xml").read_text(encoding="utf-8")
    (repo / "repository2-4.xml").write_text(
        index.replace("@SIZE@", str(package.stat().st_size)).replace("@SHA1@", digest),
        encoding="utf-8",
    )
    os.environ["SDK_TEST_BASE_URL"] = repo.as_uri() + "/"
    run(manager, "--install", "extras;local;fixture")
    assert (
        sdk / "extras/local/fixture/payload.txt"
    ).read_text() == "offline installation\n"
    assert "extras;local;fixture" in run(manager, "--list_installed").stdout
    run(manager, "--uninstall", "extras;local;fixture")
    assert not (sdk / "extras/local/fixture/payload.txt").exists()


def run(
    *args: Path | str, expected: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    command = subprocess.run(args, text=True, capture_output=True)
    if command.returncode not in expected:
        print(command.stdout)
        print(command.stderr)
        raise RuntimeError(
            f"{args[0]} returned {command.returncode}; expected {expected}"
        )
    return command


def verify_analysis(bin_dir: Path, build_tools: Path, sdk: Path, work: Path) -> None:
    aapt2 = build_tools / "aapt2"
    platform = sdk / "platforms/android-37"
    platform.mkdir(parents=True)
    (platform / "source.properties").write_text(
        "Pkg.Revision=1\nAndroidVersion.ApiLevel=37\n"
    )
    compiled = work / "framework-res.zip"
    android = platform / "android.jar"
    run(aapt2, "compile", "--dir", TESTS / "framework/res", "-o", compiled)
    run(
        aapt2,
        "link",
        "--manifest",
        TESTS / "framework/AndroidManifest.xml",
        "-o",
        android,
        compiled,
    )
    lint_report = work / "lint.txt"
    assert "lint: version " in run(bin_dir / "lint", "--version").stdout
    run(
        bin_dir / "lint",
        "--sdk-home",
        sdk,
        "--check",
        "HardcodedText",
        "--text",
        lint_report,
        TESTS / "app",
    )
    assert "[HardcodedText]" in lint_report.read_text()

    compiled = work / "app-res.zip"
    base = work / "base.apk"
    proto = work / "proto.apk"
    generated = work / "generated"
    generated.mkdir()
    run(aapt2, "compile", "--dir", TESTS / "app/res", "-o", compiled)
    common: list[Path | str] = [
        "-I",
        android,
        "--manifest",
        TESTS / "app/AndroidManifest.xml",
    ]
    run(aapt2, "link", *common, "-o", base, compiled)
    run(
        aapt2,
        "link",
        *common,
        "--proto-format",
        "--java",
        generated,
        "-o",
        proto,
        compiled,
    )

    classes = work / "classes"
    dex = work / "dex"
    classes.mkdir()
    dex.mkdir()
    run(
        "javac",
        "--release",
        "8",
        "-Xlint:-options",
        "-d",
        classes,
        generated / "demo/cmdline/R.java",
        TESTS / "ResourceUse.java",
    )
    java_jar = work / "app.jar"
    run("jar", "--create", "--file", java_jar, "-C", classes, ".")
    run(bin_dir / "d8", "--min-api", "24", "--output", dex, java_jar)
    run("jar", "--update", "--file", base, "-C", dex, "classes.dex")
    analyzer = bin_dir / "apkanalyzer"
    assert (
        run(analyzer, "manifest", "application-id", base).stdout.strip()
        == "demo.cmdline"
    )
    assert (
        "demo.cmdline.ResourceUse"
        in run(analyzer, "dex", "packages", "--defined-only", base).stdout
    )
    assert "demo.cmdline" in run(analyzer, "resources", "packages", base).stdout

    dex_zip = work / "dex.zip"
    with ZipFile(dex_zip, "w") as archive:
        archive.write(dex / "classes.dex", "classes.dex")
    shrunk = work / "shrunk.apk"
    run(
        bin_dir / "resourceshrinker",
        "--input",
        proto,
        "--dex_input",
        dex_zip,
        "--output",
        shrunk,
        "--print_usage_log",
        work / "usage.txt",
    )
    with ZipFile(shrunk) as archive:
        files = set(archive.namelist())
        assert {"res/raw/kept_by_manifest.txt", "res/raw/kept_by_dex.txt"} <= files
        assert "res/raw/unused.txt" not in files

    profile = work / "baseline.prof"
    decoded = work / "decoded.txt"
    run(
        bin_dir / "profgen",
        "bin",
        TESTS / "baseline.txt",
        "--apk",
        base,
        "--output",
        profile,
        "--profile-format",
        "V0_1_5_S",
    )
    run(
        bin_dir / "profgen",
        "dumpProfile",
        "--profile",
        profile,
        "--apk",
        base,
        "--output",
        decoded,
    )
    assert (TESTS / "baseline.txt").read_text().strip() in decoded.read_text()
    screenshot = run(bin_dir / "screenshot2", expected=(0, 1))
    assert "Usage: screenshot2" in screenshot.stdout + screenshot.stderr

    shrunk_jar = work / "shrunk.jar"
    mapping = work / "mapping.txt"
    run(
        bin_dir / "r8",
        "--classfile",
        "--release",
        "--lib",
        os.environ["JAVA_HOME"],
        "--output",
        shrunk_jar,
        "--pg-conf",
        TESTS / "keep.pro",
        "--pg-map-output",
        mapping,
        java_jar,
    )
    trace = work / "trace.txt"
    trace.write_text(
        run("java", "-cp", shrunk_jar, "demo.cmdline.ResourceUse", expected=(1,)).stderr
    )
    assert (
        "demo.cmdline.Crash.raise(" in run(bin_dir / "retrace", mapping, trace).stdout
    )


def main() -> None:
    lock = read_lock()
    version = lock["version"]
    with TemporaryDirectory(dir=OUT, prefix="verify.") as directory:
        work = Path(directory)
        sdk = work / "sdk"
        user_home = work / "user"
        user_home.mkdir()
        temp = work / "tmp"
        temp.mkdir()
        jdk = work / "jdk"
        for name, spec in lock["files"].items():
            if spec["role"] == "jdk":
                fetch(name, spec)
                unpack_tar(CACHE / name, jdk)
            if spec["role"] == "verification":
                fetch(name, spec)
                run("unzip", "-q", CACHE / name, "-d", work)
        for key in (
            "JAVA_TOOL_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "_JAVA_OPTIONS",
            "CLASSPATH",
            "ANDROID_SDK_ROOT",
            "SDK_TEST_BASE_URL",
        ):
            os.environ.pop(key, None)
        os.environ.update(
            JAVA_HOME=str(jdk),
            PATH=f"{jdk / 'bin'}{os.pathsep}{os.environ['PATH']}",
            HOME=str(user_home),
            ANDROID_USER_HOME=str(user_home / ".android"),
            ANDROID_HOME=str(sdk),
            ANDROID_AVD_HOME=str(user_home / ".android/avd"),
            JAVA_TOOL_OPTIONS=f'-Duser.home="{user_home}" -Djava.io.tmpdir="{temp}" -XX:ActiveProcessorCount=8',
            TMPDIR=str(temp),
            LC_ALL="C",
            TZ="UTC",
        )
        sdk_tools = sdk / "cmdline-tools" / version
        sdk_tools.parent.mkdir(parents=True)
        package = OUT / "dist" / f"aosstudio-cmdline-tools-{version}-linux-x86_64.zip"
        run("unzip", "-q", package, "-d", work)
        move(work / "cmdline-tools", sdk_tools)
        build_tools = sdk / "build-tools/36.0.0"
        build_tools.parent.mkdir()
        move(work / "build-tools", build_tools)
        assert (
            f"Pkg.Revision={version}"
            in (sdk_tools / "source.properties").read_text().splitlines()
        )
        assert (sdk_tools / "source-inputs.json").read_bytes() == LOCK_PATH.read_bytes()
        bin_dir = sdk_tools / "bin"
        verify_manager(bin_dir, sdk, work, version)
        verify_analysis(bin_dir, build_tools, sdk, work)
    print(f"Command-Line Tools {version} package verified")


if __name__ == "__main__":
    main()
