#!/bin/sh
set -eu

tools_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
java=java
if [ -n "${JAVA_HOME:-}" ]; then
    java="$JAVA_HOME/bin/java"
fi

case "${0##*/}" in
    sdkmanager|avdmanager)
        # The SDK installer provides its verified repository through this variable.
        case "${SDK_TEST_BASE_URL:-file:///}" in
            */) SDK_TEST_BASE_URL=${SDK_TEST_BASE_URL:-file:///} ;;
            *) SDK_TEST_BASE_URL="$SDK_TEST_BASE_URL/" ;;
        esac
        export SDK_TEST_BASE_URL
        if [ "${0##*/}" = sdkmanager ]; then
            set -- -Dcom.android.sdklib.toolsdir="$tools_dir" -cp "$tools_dir/lib/shared/*" \
                com.android.sdklib.tool.sdkmanager.SdkManagerCli "$@"
        else
            set -- -Dcom.android.sdkmanager.toolsdir="$tools_dir" -cp "$tools_dir/lib/shared/*" \
                com.android.sdklib.tool.AvdManagerCli "$@"
        fi
        ;;
    apkanalyzer)
        set -- -Dcom.android.sdklib.toolsdir="$tools_dir" -cp "$tools_dir/lib/shared/*:$tools_dir/lib/analysis/*" \
            com.android.tools.apk.analyzer.ApkAnalyzerCli "$@"
        ;;
    lint)
        set -- -Djava.awt.headless=true -cp "$tools_dir/lib/shared/*:$tools_dir/lib/analysis/*:$tools_dir/lib/lint/*" \
            com.android.tools.lint.Main "$@"
        ;;
    profgen)
        set -- -cp "$tools_dir/lib/shared/*:$tools_dir/lib/analysis/*" com.android.tools.profgen.cli.MainKt "$@"
        ;;
    resourceshrinker)
        common="$tools_dir/lib/shared/common-@LIBRARY_VERSION@.jar"
        sdk_common="$tools_dir/lib/analysis/sdk-common-@LIBRARY_VERSION@.jar"
        set -- -Dcom.android.sdklib.toolsdir="$tools_dir" \
            -cp "$common:$sdk_common:$tools_dir/lib/shrinker/*:$tools_dir/r8/lib/*:$tools_dir/r8/lib/r8-runtime/*" \
            com.android.build.shrinker.ResourceShrinkerCli "$@"
        ;;
    screenshot2)
        set -- -Dcom.android.sdklib.toolsdir="$tools_dir" -cp "$tools_dir/lib/shared/*:$tools_dir/lib/analysis/*" \
            com.android.screenshot.Screenshot "$@"
        ;;
    d8|r8|retrace)
        exec "$tools_dir/r8/bin/${0##*/}" "$@"
        ;;
    *)
        printf 'Unknown command: %s\n' "${0##*/}" >&2
        exit 2
        ;;
esac

exec "$java" "$@"
