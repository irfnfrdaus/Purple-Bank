"""Install pinned Android build tools outside the checkout, retaining verification."""
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request
from urllib.parse import urlparse
import zipfile

TOOLS = Path("/workspace/tools")
SDK = TOOLS / "android-sdk"


def download(url, path, expected, algorithm="sha256"):
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    if hashlib.new(algorithm, path.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Checksum mismatch for {path.name}; artifact not used")


TOOLS.mkdir(parents=True, exist_ok=True)
# The base image has a JRE without jlink; Android compilation needs a complete JDK.
jdk_archive = TOOLS / "temurin-jdk-21.0.13_9.tar.gz"
# SHA-256 published alongside the official Eclipse Adoptium release.
jdk_url = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz"
download(jdk_url, jdk_archive, "d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c")
with tarfile.open(jdk_archive) as archive:
    jdk_folder = archive.getnames()[0].split("/")[0]
    if not (TOOLS / jdk_folder / "bin/jlink").exists():
        archive.extractall(TOOLS, filter="data")
(TOOLS / "jdk-current.txt").write_text(str(TOOLS / jdk_folder))
cli = TOOLS / "android-cli-12.zip"
download("https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip",
         cli, "d313adb7aedccf6cf0cfca51ec180f0059f5f8f8", "sha1")
# Published checksum comes from Google's repository2-1.xml, cmdline-tools;12.0.
target = SDK / "cmdline-tools" / "12.0"
if not target.exists():
    staging = TOOLS / "android-cli-staging"
    staging.mkdir(exist_ok=True)
    with zipfile.ZipFile(cli) as archive:
        archive.extractall(staging)
    target.parent.mkdir(parents=True, exist_ok=True)
    (staging / "cmdline-tools").rename(target)
for executable in (target / "bin").iterdir():
    executable.chmod(0o755)

gradle_zip = TOOLS / "gradle-8.9-bin.zip"
checksum = urllib.request.urlopen("https://downloads.gradle.org/distributions/gradle-8.9-bin.zip.sha256").read().decode().strip()
download("https://downloads.gradle.org/distributions/gradle-8.9-bin.zip", gradle_zip, checksum)
if not (TOOLS / "gradle-8.9").exists():
    with zipfile.ZipFile(gradle_zip) as archive:
        archive.extractall(TOOLS)
(TOOLS / "gradle-8.9/bin/gradle").chmod(0o755)

environment = os.environ.copy()
environment["ANDROID_USER_HOME"] = str(TOOLS / ".android")
proxy = urlparse(environment.get("HTTPS_PROXY", ""))
options = "-Djava.util.prefs.userRoot=/workspace/tools/java-prefs"
if proxy.hostname:
    options += f" -Dhttps.proxyHost={proxy.hostname} -Dhttps.proxyPort={proxy.port or 80} -Dhttp.proxyHost={proxy.hostname} -Dhttp.proxyPort={proxy.port or 80}"
environment["SDKMANAGER_OPTS"] = options
manager = [str(target / "bin/sdkmanager"), f"--sdk_root={SDK}"]
# Normal Android SDK licence acceptance for building this requested Android app.
subprocess.run([*manager, "--licenses"], input="y\n" * 200, text=True, env=environment, check=True)
subprocess.run([*manager, "platforms;android-35", "build-tools;34.0.0", "build-tools;35.0.0", "platform-tools"],
               input="y\n" * 100, text=True, env=environment, check=True)
print("Verified Gradle 8.9 and Android SDK 35 are installed in /workspace/tools")
