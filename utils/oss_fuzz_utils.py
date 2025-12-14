import yaml
from constants import LanguageType
from pathlib import Path
from typing import Optional
import os

class OSSFuzzUtils:
    def __init__(self, ossfuzz_dir: Path, benchmark_dir: Path, project_name: str, new_project_name: str) -> None:

        self.ossfuzz_dir = ossfuzz_dir
        self.yaml_path = benchmark_dir / "{}.yaml".format(project_name)
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.language = self.get_project_language()

    def get_project_language(self) -> LanguageType:
        file_path = self.ossfuzz_dir / "projects" / self.project_name / "project.yaml"
        with open(file_path, 'r') as file:
            try:
                data = yaml.safe_load(file)
                language = data.get("language")
                if language == "c++":
                    return LanguageType.CPP
                else:
                    return LanguageType(language.upper())

            except yaml.YAMLError as e:
                print(f"Error parsing YAML file: {e}")
                return LanguageType.NONE

    def get_script_cmd(self, mode: str="build_image") -> list[str]:

        mapping: dict[str, list[str]] = {

            # --clean will remove /out and /work directories
            "build_fuzzers":  ["python", os.path.join(self.ossfuzz_dir, "infra", "helper.py"),
                            "build_fuzzers", "--clean", self.new_project_name],

            "build_image": ["python", os.path.join(self.ossfuzz_dir, "infra", "helper.py"),
                            "build_image", self.new_project_name, "--pull"]
        }
        assert mode in mapping.keys()
        return mapping.get(mode) # type: ignore

    def get_path(self, mode: str ="build_script") -> Path:
        mapping = {
            "build_script": self.ossfuzz_dir / "projects"/ self.new_project_name /"build.sh",
            "fuzzer": self.ossfuzz_dir / "build" / "out" /self.new_project_name,
        }

        assert mode in mapping.keys()
        return mapping.get(mode) # type: ignore

    def get_harness_and_fuzzer(self) -> tuple[str, Path]:
        """Returns the harness and fuzzer file path for the project."""

        with open(self.yaml_path, 'r') as file:
            try:
                data = yaml.safe_load(file)
                return data.get("target_name"), Path(data.get("target_path"))
            except yaml.YAMLError as e:
                print(f"Error parsing YAML file: {e}")
                return "", Path("")

    def get_extension(self, file_path: Optional[Path]) -> LanguageType:
        """Returns the file type based on the extension of |file_name|."""
       
        if file_path is None:
            file_path = Path(self.get_harness_and_fuzzer()[1])
            
        file_name = file_path.name
        if file_name.endswith('.c'):
            return LanguageType.C
        cpp_extensions = ['.cc', '.cpp', '.cxx', '.c++', '.h', '.hpp']
        if any(file_name.endswith(ext) for ext in cpp_extensions):
            return LanguageType.CPP
        if file_name.endswith('.java'):
            return LanguageType.JAVA
        return LanguageType.NONE