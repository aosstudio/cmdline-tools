package demo.cmdline;

public final class ResourceUse {
    public static int keptInDex() {
        return R.raw.kept_by_dex;
    }

    public static void main(String[] args) {
        Crash.raise();
    }
}

final class Crash {
    static void raise() {
        throw new IllegalStateException("Retrace fixture");
    }
}
