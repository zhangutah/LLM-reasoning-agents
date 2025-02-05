
import os
import yaml
from constants import LanguageType, PROJECT_PATH

class OSSFuzzUtils:
    def __init__(self, ossfuzz_dir: str, project_name: str, new_project_name: str) -> None:

        self.ossfuzz_dir = ossfuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.language = self.get_project_language()

    def get_project_language(self) -> str:
        file_path = os.path.join(self.ossfuzz_dir, "projects", self.project_name, "project.yaml")
        with open(file_path, 'r') as file:
            try:
                data = yaml.safe_load(file)
                language = data.get("language")
                if language == "c++":
                    return LanguageType.CPP
                else:
                    return language.upper()

            except yaml.YAMLError as e:
                return None

    def get_script_cmd(self, mode: str="build_image") -> list[str]:
        mapping = {
            "build_fuzzers":  ["python", os.path.join(self.ossfuzz_dir, "infra", "helper.py"),
                            "build_fuzzers", self.new_project_name],
            "build_image": ["python", os.path.join(self.ossfuzz_dir, "infra", "helper.py"),
                            "build_image", self.new_project_name, "--pull", "--cache"],
            # "run_fuzzer": ["python", os.path.join(self.oss_fuzz_dir, "infra", "helper.py"),]

        }

        return mapping.get(mode)


    def get_path(self, mode: str ="build_script") -> str:
        mapping = {
            "build_script": os.path.join(self.ossfuzz_dir, "projects", self.new_project_name, "build.sh"),
            "fuzzer": os.path.join(self.ossfuzz_dir, "build/out/", self.new_project_name),
        }

        return mapping.get(mode)

    def get_harness_and_fuzzer(self) -> tuple[str, str]:
        """Returns the harness and fuzzer file path for the project."""

        benchmark_yaml = "{}/benchmark-sets/all/{}.yaml".format(PROJECT_PATH, self.project_name)

        with open(benchmark_yaml, 'r') as file:
            try:
                data = yaml.safe_load(file)
                return data.get("target_name"), data.get("target_path")
            except yaml.YAMLError as e:
                return None

    def get_extension(self, file_path: str = None):
        """Returns the file type based on the extension of |file_name|."""
       
        if file_path is None:
            file_path = self.get_harness_and_fuzzer()[1]
            
        if file_path.endswith('.c'):
            return LanguageType.C
        cpp_extensions = ['.cc', '.cpp', '.cxx', '.c++', '.h', '.hpp']
        if any(file_path.endswith(ext) for ext in cpp_extensions):
            return LanguageType.CPP
        if file_path.endswith('.java'):
            return LanguageType.JAVA
        return LanguageType.NONE