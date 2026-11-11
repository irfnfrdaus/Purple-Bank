"""Build native APK with the cloud proxy, SDK and caches outside the checkout."""
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

root = Path(__file__).resolve().parent.parent
tools = Path("/workspace/tools")
environment = os.environ.copy()
environment.update({"ANDROID_HOME": str(tools / "android-sdk"),
                    "ANDROID_USER_HOME": str(tools / ".android"),
                    "GRADLE_USER_HOME": str(tools / "gradle-home")})
environment["JAVA_HOME"] = (tools / "jdk-current.txt").read_text().strip()
gradle = tools / "gradle-8.9/bin/gradle"
if not gradle.exists() or not (tools / "android-sdk/platforms/android-35").exists():
    raise SystemExit("Run python scripts/install_android.py first")
proxy = urlparse(environment.get("HTTPS_PROXY", ""))
options = ["-Djavax.net.ssl.trustStore=/etc/ssl/certs/java/cacerts"]
if proxy.hostname:
    for scheme in ["http", "https"]:
        options.extend([f"-D{scheme}.proxyHost={proxy.hostname}", f"-D{scheme}.proxyPort={proxy.port or 80}"])
subprocess.run([str(gradle), *options, "--no-daemon", "--max-workers=2", "assembleDebug", "lintDebug"],
               cwd=root / "android", env=environment, check=True)
artifacts = root / ".runtime/artifacts"
artifacts.mkdir(parents=True, exist_ok=True)
shutil.copy2(root / "android/app/build/outputs/apk/debug/app-debug.apk", artifacts / "PurpleBank-debug.apk")
print(f"APK: {artifacts / 'PurpleBank-debug.apk'}")
