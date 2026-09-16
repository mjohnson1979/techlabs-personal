"""Install checksum-pinned Linux x64 catalog tools at image build time."""

import hashlib
import io
import os
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile


def install() -> None:
    tools = (
        ("bicep", "https://github.com/Azure/bicep/releases/download/v0.38.33/bicep-linux-x64",
         "07e28411c2cb3a731660a32bb735268c2d1506c564bcea613eed1323e9926712"),
        ("terraform", "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip",
         "186e0145f5e5f2eb97cbd785bc78f21bae4ef15119349f6ad4fa535b83b10df8"),
    )
    for name, url, expected in tools:
        with urlopen(url, timeout=120) as response:
            content = response.read(150 * 1024 * 1024 + 1)
        if len(content) > 150 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{name} download failed integrity validation")
        if name == "terraform":
            with ZipFile(io.BytesIO(content)) as archive:
                license_path = Path("/usr/local/share/licenses/terraform/LICENSE.txt")
                license_path.parent.mkdir(parents=True, exist_ok=True)
                license_path.write_bytes(archive.read("LICENSE.txt"))
                content = archive.read("terraform")
        destination = Path("/usr/local/bin") / name
        destination.write_bytes(content)
        os.chmod(destination, 0o755)


if __name__ == "__main__":
    install()