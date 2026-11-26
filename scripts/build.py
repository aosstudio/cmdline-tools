"""Build and package the Command-Line Tools from pinned inputs."""

from base64 import b64encode
from collections.abc import Iterable, Sequence
import os
from pathlib import Path
import platform
import re
from shutil import copyfile, copyfileobj, move, rmtree
import subprocess
import tarfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from inputs import CACHE, LOCK_PATH, OUT, ROOT, Lock, fetch_all, read_lock

WORK = OUT / "build"
TMP = WORK / "tmp"
JDK = WORK / "jdk"
STAGE = WORK / "cmdline-tools"
LIB = STAGE / "lib"
NOTICES = STAGE / "licenses"
COMPILER = WORK / "compiler"
SOURCES = WORK / "sources"
JAR_DATE = "2026-06-05T00:00:00Z"
ICC_PROFILES = {
    "ICC_PROFILE_DISPLAY_P3": "DisplayP3-v4.icc",
    "ICC_PROFILE_SRGB": "sRGB-v2-magic.icc",
}
LINT_DATA = {"desugared_apis_30_1.txt", "plural-examples.txt", "private-apis.txt"}


def unpack_zip(
    source: Path, destination: Path, names: Iterable[str] | None = None
) -> None:
    with ZipFile(source) as archive:
        archive.extractall(destination, members=names)


def unpack_tar(source: Path, destination: Path) -> None:
    with tarfile.open(source) as archive:
        archive.extractall(destination, filter="data")


def jdk_command(tool: str, args: Sequence[Path | str]) -> None:
    environment = os.environ | {
        "JAVA_HOME": str(JDK),
        "JAVA_TOOL_OPTIONS": f'-XX:ActiveProcessorCount=8 -Djava.io.tmpdir="{TMP}"',
        "JDK_JAVA_OPTIONS": "",
        "_JAVA_OPTIONS": "",
        "CLASSPATH": "",
        "LC_ALL": "C",
        "TZ": "UTC",
        "TMPDIR": str(TMP),
    }
    subprocess.run([JDK / "bin" / tool, *args], env=environment, check=True)


def jar(classes: Path, destination: Path) -> None:
    jdk_command(
        "jar",
        ["--create", "--date", JAR_DATE, "--file", destination, "-C", classes, "."],
    )


def compile_sources(
    name: str,
    roots: Sequence[Path],
    classes: Path,
    dependencies: Sequence[Path],
    *,
    processor: Path | None = None,
) -> None:
    classes.mkdir(parents=True, exist_ok=True)
    classpath = os.pathsep.join(map(str, (classes, *dependencies)))
    kotlin = sorted({file for root in roots for file in root.rglob("*.kt")})
    java = sorted({file for root in roots for file in root.rglob("*.java")})
    print(f"Building {name} ({len(kotlin)} Kotlin, {len(java)} Java)", flush=True)
    for tool, files in (("kotlin", kotlin), ("javac", java)):
        if not files:
            continue
        arguments = WORK / f"{name}-{tool}.args"
        arguments.write_text(
            "".join(f'"{source.as_posix()}"\n' for source in files), encoding="utf-8"
        )
        if tool == "kotlin":
            jdk_command(
                "java",
                [
                    "-Xmx5g",
                    "-cp",
                    COMPILER / "*",
                    "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                    "-no-stdlib",
                    "-no-reflect",
                    "-jvm-target",
                    "17",
                    "-language-version",
                    "2.2",
                    "-classpath",
                    classpath,
                    "-Xjava-source-roots=" + ",".join(map(str, roots)),
                    "-module-name",
                    name,
                    "-d",
                    classes,
                    f"@{arguments}",
                ],
            )
        else:
            annotation_processing = (
                [
                    "-processorpath",
                    str(processor),
                    "-processor",
                    "com.google.auto.value.processor.AutoValueProcessor",
                ]
                if processor
                else ["-proc:none"]
            )
            jdk_command(
                "javac",
                [
                    "-J-Xmx4g",
                    "--release",
                    "17",
                    "-encoding",
                    "UTF-8",
                    *annotation_processing,
                    "-classpath",
                    classpath,
                    "-d",
                    classes,
                    f"@{arguments}",
                ],
            )


def sources(name: str, archive: str) -> Path:
    destination = SOURCES / name
    if archive.endswith(".jar"):
        with ZipFile(CACHE / archive) as source:
            source.extractall(
                destination,
                [
                    member
                    for member in source.namelist()
                    if member.endswith((".java", ".kt"))
                ],
            )
    else:
        unpack_tar(CACHE / archive, destination)
    return destination


def resources(source: str, classes: Path) -> None:
    with ZipFile(CACHE / source) as archive:
        archive.extractall(
            classes,
            [
                name
                for name in archive.namelist()
                if name == "NOTICE"
                or name == "META-INF/gradle-declarative-lang.xml"
                or (
                    source.startswith("lint-checks-")
                    and (name in LINT_DATA or name.startswith("typos/"))
                )
                or (
                    not source.startswith("lint-")
                    and name.endswith(
                        (".properties", ".template", ".xml", ".xjb", ".xsd")
                    )
                )
            ],
        )


def stage_inputs(lock: Lock) -> None:
    for group in ("shared", "analysis", "lint", "shrinker"):
        (LIB / group).mkdir(parents=True)
    NOTICES.mkdir()
    destinations = {
        "core-library": LIB / "shared",
        "analysis-library": LIB / "analysis",
        "lint-library": LIB / "lint",
        "shrinker-library": LIB / "shrinker",
    }
    for name, spec in lock["files"].items():
        role = spec["role"]
        if role in destinations:
            copyfile(CACHE / name, destinations[role] / name)
        elif role == "jdk":
            unpack_tar(CACHE / name, JDK)
        elif role == "r8":
            unpack_tar(CACHE / name, WORK)
            move(WORK / name.removesuffix(".tar.gz"), STAGE / "r8")
        elif role == "notice" and not name.endswith(".jar"):
            copyfile(CACHE / name, NOTICES / name)

    with tarfile.open(CACHE / "icc-profiles.tar.gz") as archive:
        profiles = next(iter(archive)).name
        for name in ICC_PROFILES.values():
            archive.extract(f"{profiles}/profiles/{name}", WORK, filter="data")
        archive.extract(f"{profiles}/license", WORK, filter="data")
    move(WORK / profiles, WORK / "icc")
    copyfile(WORK / "icc/license", NOTICES / "ICC-CC0")

    gradle = "gradle-9.6.0"
    prefix = f"{gradle}/lib/"
    distribution = CACHE / f"{gradle}-bin.zip"
    with ZipFile(distribution) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.startswith(prefix)
            and name.endswith(".jar")
            and "/" not in name[len(prefix) :]
        ]
    unpack_zip(distribution, WORK, names)
    move(WORK / prefix, COMPILER)
    copyfile(COMPILER / "groovy-4.0.32.jar", LIB / "lint/groovy-4.0.32.jar")
    copyfile(LOCK_PATH, STAGE / "source-inputs.json")
    copyfile(ROOT / "LICENSE", STAGE / "LICENSE")


def build_manager(version: str) -> None:
    for name in ("common", "repository", "sdklib"):
        basename = f"{name}-{version}"
        source = sources(name, f"{basename}-sources.jar")
        if name == "sdklib":
            subprocess.run(
                [
                    "patch",
                    "--batch",
                    "--forward",
                    "--fuzz=0",
                    "-p1",
                    "-d",
                    source,
                    "-i",
                    ROOT / "patches/sdkmanager-launcher-notice.patch",
                ],
                check=True,
            )
        classes = WORK / "classes" / name
        compile_sources(name, [source], classes, sorted((LIB / "shared").glob("*.jar")))
        resources(f"{basename}.jar", classes)
        jar(classes, LIB / "shared" / f"{basename}.jar")


def embed_profiles(source: Path) -> None:
    # The embedded upstream profiles have separate copyright statements.
    # Replace them with the original CC0 profile bytes using the same encoding.
    file = source / "com/android/screenshot/Screenshot.java"
    java = file.read_text(encoding="utf-8")
    for name, profile in ICC_PROFILES.items():
        encoded = b64encode((WORK / "icc/profiles" / profile).read_bytes()).decode(
            "ascii"
        )
        java, count = re.subn(
            rf"(private static final String {name}\s*=)[^;]+;",
            rf'\1 "{encoded}";',
            java,
        )
        if count != 1:
            raise ValueError(f"Expected one embedded ICC profile for {name}")
    file.write_text(java, encoding="utf-8")


def build_analysis(version: str) -> None:
    analyzer = sources("apkanalyzer", "apkanalyzer.tar.gz")
    analyzer_library = sources("analyzer-library", f"apkanalyzer-{version}-sources.jar")
    binary_resources = sources(
        "binary-resources", f"binary-resources-{version}-sources.jar"
    )
    (archive_patcher,) = sources(
        "archive-patcher", "archive-patcher-source.tar.gz"
    ).iterdir()
    analysis = LIB / "analysis"
    deps = sorted((LIB / "shared").glob("*.jar")) + sorted(analysis.glob("*.jar"))
    classes = WORK / "classes/apkanalyzer"
    compile_sources(
        "archive-patcher",
        [
            archive_patcher / module / "src/main/java"
            for module in ("shared", "generator", "explainer")
        ],
        classes,
        [],
    )
    copyfile(archive_patcher / "LICENSE", NOTICES / "archive-patcher-LICENSE")
    compile_sources(
        "binary-resources",
        [binary_resources],
        classes,
        deps,
        processor=CACHE / "auto-value-1.10.1.jar",
    )
    compile_sources("apkanalyzer", [analyzer_library, analyzer], classes, deps)
    for module in ("apkanalyzer", "binary-resources"):
        with ZipFile(CACHE / f"{module}-{version}.jar") as archive:
            (NOTICES / f"{module}-library-NOTICE").write_bytes(archive.read("NOTICE"))
    jar(classes, analysis / "apkanalyzer-source.jar")

    screenshot = sources("screenshot2", "screenshot2-source.tar.gz")
    embed_profiles(screenshot)
    screenshot_classes = WORK / "classes/screenshot2"
    compile_sources("screenshot2", [screenshot], screenshot_classes, deps)
    jar(screenshot_classes, analysis / "screenshot2-source.jar")

    profgen = sources("profgen", "profgen.tar.gz")
    profgen_cli = sources("profgen-cli", "profgen-cli.tar.gz")
    with ZipFile(CACHE / "gradle-9.5.2-sources.jar") as archive:
        serializer = Path("com/android/tools/profgen/ArtProfileSerializer.kt")
        if (
            archive.read(serializer.as_posix())
            != (profgen / "kotlin" / serializer).read_bytes()
        ):
            raise ValueError(
                "Profgen serializer differs from the licensed upstream source archive"
            )
        (NOTICES / "profgen-AGP-NOTICE").write_bytes(archive.read("NOTICE"))
    profgen_classes = WORK / "classes/profgen"
    compile_sources("profgen", [profgen, profgen_cli], profgen_classes, deps)
    jar(profgen_classes, analysis / "profgen-source.jar")

    shrinker = sources("resourceshrinker", "resourceshrinker.tar.gz")
    shrinker_classes = WORK / "classes/resourceshrinker"
    r8 = STAGE / "r8/lib"
    shrinker_dependencies = [
        *sorted((LIB / "shrinker").glob("*.jar")),
        *r8.glob("r8-*.jar"),
    ]
    shrinker_dependencies.extend(sorted((r8 / "r8-runtime").glob("*.jar")))
    compile_sources(
        "resourceshrinker", [shrinker], shrinker_classes, deps + shrinker_dependencies
    )
    jar(shrinker_classes, LIB / "shrinker/resourceshrinker-source.jar")


def build_lint(version: str) -> None:
    lint = LIB / "lint"
    deps = sorted((LIB / "shared").glob("*.jar")) + sorted(
        (LIB / "analysis").glob("*.jar")
    )
    for name in ("lint-model", "lint-api", "lint-checks", "lint"):
        basename = f"{name}-{version}"
        source = sources(name, f"{basename}-sources.jar")
        classes = WORK / "classes" / name
        roots = [source]
        if name == "lint":
            # Kotlin uses package roots to resolve the generated Java PSI.
            roots.append(source / "tools/adt/idea/gradle-declarative-lang/gen")
        compile_sources(name, roots, classes, deps + sorted(lint.glob("*.jar")))
        resources(f"{basename}.jar", classes)
        jar(classes, lint / f"{basename}-source.jar")


def package(version: str, library_version: str) -> None:
    bin_dir = STAGE / "bin"
    bin_dir.mkdir()
    launcher = (ROOT / "scripts/launcher.sh").read_text(encoding="utf-8")
    launcher = launcher.replace("@LIBRARY_VERSION@", library_version)
    for name in (
        "sdkmanager",
        "avdmanager",
        "apkanalyzer",
        "lint",
        "profgen",
        "resourceshrinker",
        "screenshot2",
        "d8",
        "r8",
        "retrace",
    ):
        file = bin_dir / name
        file.write_text(launcher, encoding="utf-8")
        file.chmod(0o755)

    (STAGE / "source.properties").write_text(
        f"Pkg.Desc=AOSStudio Command-Line Tools\nPkg.Revision={version}\nPkg.UserSrc=false\n",
        encoding="utf-8",
    )
    dist = OUT / "dist"
    dist.mkdir(exist_ok=True)
    name = f"aosstudio-cmdline-tools-{version}-linux-x86_64.zip"
    pending = WORK / name
    with ZipFile(pending, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                info = ZipInfo(path.relative_to(WORK).as_posix(), (2026, 6, 5, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.compress_level = 6
                info.external_attr = path.stat().st_mode << 16
                with path.open("rb") as source, archive.open(info, "w") as member:
                    copyfileobj(source, member)
    pending.replace(dist / name)
    print(dist / name)


def main() -> None:
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        raise SystemExit("This build targets Linux x86_64.")
    lock = read_lock()
    fetch_all(lock)
    if WORK.exists():
        rmtree(WORK)
    WORK.mkdir()
    TMP.mkdir()
    stage_inputs(lock)
    version = lock["library_version"]
    build_manager(version)
    build_analysis(version)
    build_lint(version)
    package(lock["version"], version)


if __name__ == "__main__":
    main()
