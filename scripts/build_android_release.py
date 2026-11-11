"""Build a signed, non-debuggable Android release with a persistent external key.

Requires an existing JDK, Android SDK 35, and Gradle 8.9 (or the wrapper).
Never generates, replaces, prints or publishes the signing key.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    environment = os.environ.copy()
    sdk = Path(environment.get("ANDROID_HOME", environment.get("ANDROID_SDK_ROOT", "")))
    if not sdk.is_absolute() or not (sdk / "platforms/android-35/android.jar").is_file():
        raise SystemExit("Set ANDROID_HOME to the existing Android SDK 35.")
    key = Path(environment.get("PURPLE_SIGNING_KEYSTORE", ""))
    password_file = Path(environment.get("PURPLE_SIGNING_PASSWORD_FILE", ""))
    if not key.is_file() or not password_file.is_file():
        raise SystemExit("Provide an existing external signing keystore and password file; keys are never regenerated.")
    if key.resolve().is_relative_to(ROOT) or password_file.resolve().is_relative_to(ROOT):
        raise SystemExit("Keep signing material outside the repository.")
    password = password_file.read_text().strip()
    if not password:
        raise SystemExit("The signing password file is empty.")
    portal = environment.get("PURPLE_WEB_PORTAL_URL", "")
    if portal:
        parsed = urlparse(portal)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SystemExit("Use a credential-free HTTPS URL for the private browser portal.")
    environment["PURPLE_SIGNING_KEYSTORE"] = str(key.resolve())
    environment["PURPLE_SIGNING_PASSWORD"] = password
    gradle = environment.get("PURPLE_GRADLE_BIN", str(ROOT / "android/gradlew"))
    subprocess.run(
        [gradle, "--no-daemon", "--max-workers=1",
         "-Dorg.gradle.jvmargs=-Xmx384m -XX:MaxMetaspaceSize=384m -Dfile.encoding=UTF-8",
         "assembleRelease", "lintRelease", "testReleaseUnitTest"],
        cwd=ROOT / "android", env=environment, check=True,
    )
    artifact = ROOT / "android/app/build/outputs/apk/release/app-release.apk"
    tools = sdk / "build-tools/35.0.0"
    subprocess.run([str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(artifact)],
                   env=environment, check=True)
    details = subprocess.check_output([str(tools / "aapt"), "dump", "badging", str(artifact)],
                                      env=environment, text=True)
    package = re.search(r"package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", details)
    if not package or package.group(1) != "my.edu.purplebank" or "application-debuggable" in details:
        raise SystemExit("Unexpected package or debuggable APK; no release copy was produced.")
    version = package.group(3)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        raise SystemExit("Invalid release version.")
    output = args.output or ROOT / ".runtime/artifacts" / f"PurpleBank-{version}.apk"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.resolve() != artifact.resolve():
        shutil.copy2(artifact, output)
    print(f"APK: {output}")
    print(f"Package: {package.group(1)}; versionCode: {package.group(2)}")
    print(f"SHA-256: {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
