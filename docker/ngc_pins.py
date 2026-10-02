"""Print pins for the packages NVIDIA compiled for this GPU, at the versions the NGC image ships.
The Dockerfile passes them to pip as constraints, so nothing can quietly replace them."""

import importlib.metadata as md

for name in ("torch", "torchvision", "torchaudio", "triton", "numpy"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        pass
