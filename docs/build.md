# Build Command-Line Tools

Build on Linux x86-64 with Python 3.14, GNU Make, Bash, curl, flock, patch, and unzip. The scripts obtain the pinned AOSStudio JDK, Gradle distribution, and Maven artifacts from the HTTPS URLs in the [input lock](../locks/inputs.json).

```sh
make build
make verify
```

`make fetch` downloads and checks all inputs separately. The repository's disk-backed `out/` directory holds the source archives, intermediate files, and package. The build works without a preinstalled SDK or a local AOSP checkout.

The build compiles SDK Manager, AVD Manager, APK Analyzer, Lint, Profgen, Resource Shrinker, and Screenshot from pinned sources. It installs D8, R8, and Retrace from the AOSStudio R8 Release. Screenshot embeds the published CC0 sRGB and Display P3 ICC profiles. Lint retains the upstream spelling dictionaries and API and pluralization data with their notices. The source lock and applicable upstream notices are included in the distribution.

The output is `out/dist/aosstudio-cmdline-tools-24.0-linux-x86_64.zip`. Install the contents of its `cmdline-tools/` directory at `<sdk>/cmdline-tools/24.0/`. The launchers use `JAVA_HOME` when set and `java` on `PATH` otherwise. The SDK installer supplies the verified repository via `SDK_TEST_BASE_URL`. With no URL configured, the package managers use a local empty repository by default.

Verification extracts the completed package, installs and removes a fixture from a local SDK repository, checks Lint output, inspects and shrinks APKs, round-trips an ART profile, and tests D8, R8, and Retrace. The Build Tools Release supplies AAPT2 for these fixtures. Screenshot verification checks the launcher usage without connecting a device.

When a dependency's Nightly asset changes, confirm the new asset against its GitHub Release digest and update the input lock before building with it.
