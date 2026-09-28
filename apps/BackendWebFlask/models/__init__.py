from importlib import import_module
from pathlib import Path


for model_file in sorted(Path(__file__).parent.glob("*.py")):
    if model_file.stem != "__init__":
        import_module(f"{__name__}.{model_file.stem}")