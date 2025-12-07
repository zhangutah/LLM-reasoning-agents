#!/usr/bin/env python3
"""
Coverage Docker - Build and collect coverage using OSS-Fuzz Docker tools.

Usage:
    python coverage_docker.py libssh                    # Full pipeline
    python coverage_docker.py libssh --skip-build       # Skip build
    python coverage_docker.py libssh --merge-only       # Only merge & export
    
Output: coverage_output/<project>/coverage.json
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.docker_utils import DockerUtils
from constants import LanguageType, DockerResults


class CoverageDocker:
    """Build fuzzers with coverage and export coverage data using Docker."""
    
    def __init__(self, project: str, oss_fuzz_dir: Path):
        self.project = project
        # self.new_project = "{}_cov".format(project)
        self.new_project = project

        self.oss_fuzz_dir = oss_fuzz_dir
        # self.copy_project()
        self.reset_base_image()
        
        self.helper = oss_fuzz_dir / "infra" / "helper.py"
        self.build_out = oss_fuzz_dir / "build" / "out" / self.new_project
        
        self.docker = DockerUtils(
            ossfuzz_dir=oss_fuzz_dir,
            project_name=project,
            new_project_name=self.new_project,
            project_lang=LanguageType.C
        )
        self._container_id: Optional[str] = None
    
    def copy_project(self):
        """Copy project source to new project for coverage build."""
        src_dir = self.oss_fuzz_dir / "projects" / self.project
        dest_dir = self.oss_fuzz_dir / "projects" / self.new_project
        
        if dest_dir.exists():
            subprocess.run(["rm", "-rf", str(dest_dir)], check=True)
        
        subprocess.run(["cp", "-r", str(src_dir), str(dest_dir)], check=True)

        # modify the dockerfile
        dockerfile = dest_dir / "Dockerfile"
        content = dockerfile.read_text()

        # replace FROM gcr.io/oss-fuzz-base/base-builder@sha256:xxx
        # with FROM gcr.io/oss-fuzz-base/base-builder
        # use the latest base-builder to build coverage
        new_content = content.replace(
            "FROM gcr.io/oss-fuzz-base/base-builder@sha256:d34b94e3cf868e49d2928c76ddba41fd4154907a1a381b3a263fafffb7c3dce0",
            "FROM gcr.io/oss-fuzz-base/base-builder"
        )
        
        dockerfile.write_text(new_content)

    def reset_base_image(self):
        # modify the dockerfile
        dest_dir = self.oss_fuzz_dir / "projects" / self.project
        dockerfile = dest_dir / "Dockerfile"
        content = dockerfile.read_text()

        # replace FROM gcr.io/oss-fuzz-base/base-builder@sha256:xxx
        # with FROM gcr.io/oss-fuzz-base/base-builder
        # use the latest base-builder to build coverage
        new_content = content.replace(
            "FROM gcr.io/oss-fuzz-base/base-builder@sha256:d34b94e3cf868e49d2928c76ddba41fd4154907a1a381b3a263fafffb7c3dce0",
            "FROM gcr.io/oss-fuzz-base/base-builder"
        )
        
        dockerfile.write_text(new_content)


    def build_cov_fuzzer(self) -> bool:
        """Step 1: Build fuzzers with sanitizer=coverage."""
        print("[*] Building coverage fuzzers...")
        cmd = ["python", str(self.helper), "build_fuzzers", 
               "--clean", "--sanitizer=coverage", self.new_project]
        if not self.docker.build_fuzzers(cmd):
            print("[-] Failed to build fuzzers")
            return False
        
        return True
    
    def run_coverage(self) -> bool:
        """Step 2: Run coverage collection (--no-serve)."""
        print("[*] Running coverage collection...")
        cmd = ["python", str(self.helper), "coverage","--public", "--no-serve", self.new_project]
        try:
            subprocess.run(cmd, cwd=str(self.oss_fuzz_dir), check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"[-] Coverage failed: {e}")
            return False
    
    def merge_profdata(self) -> bool:
        """Step 3: Merge .profraw files in Docker container."""
        print("[*] Merging .profdata files...")
        # Start container
        # # Merge all profraw files for the ssh_server_fuzzer (or all of them)
        # llvm-profdata merge -sparse /out/dumps/*.profraw -o my_coverage.profdata
        cmd = ["llvm-profdata", "merge", "-sparse", "/out/dumps/*.profdata", "-o", "/out/merged.profdata"]
        msg = self.docker.run_cmd(cmd, timeout=60, volumes={self.build_out: {"bind": "/out", "mode": "rw"}})
        if msg.startswith(DockerResults.Error.value):
            print(f"[-] Merge failed: {msg}")
            return False
        return True
    
    def export_coverage(self) -> Optional[dict]:
        """Step 4: Export coverage with llvm-cov in Docker."""
        print("[*] Exporting coverage data...")
        # find fuzzers based on profdata
        res = subprocess.run(["ls", self.build_out / "dumps"], timeout=30, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[-] Listing dumps failed: {res.stderr}")
            return None
        
        fuzzers = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line.endswith(".profdata"):
                continue
            if line.startswith("merged"):
                continue
            # assume fuzzer name is prefix before first underscore
            fuzzer_name = line[:-9]  # remove .profdata
            fuzzer_path = f"/out/{fuzzer_name}"
            if fuzzer_path not in fuzzers:
                fuzzers.append(fuzzer_path)
        if not fuzzers:
            print("[-] No fuzzers found for coverage export")
            return None
        
        export_cmd = ["llvm-cov", "export", "-format=text", "-instr-profile=/out/dumps/merged.profdata", fuzzers[0]]
        for fuzzer in fuzzers[1:]:
            export_cmd.extend(["-object", fuzzer])

        # Build shell command string with proper redirection
        # Pass as string so Docker SDK runs it via /bin/sh -c
        shell_cmd = " ".join(export_cmd) + " > /out/func_coverage.json"
        shell_cmd = f"bash -c '{shell_cmd}'"

        print(shell_cmd)
        print("[*] Exporting coverage data...")
        result = self.docker.run_cmd(shell_cmd, timeout=300, volumes={self.build_out: {"bind": "/out", "mode": "rw"}})
        
        if result.startswith(DockerResults.Error.value):
            print(f"[-] Export failed: {result}")
            return None
        print("[+] Coverage export completed.")
    
    def run(self) -> Optional[dict]:
        """Run complete pipeline."""
        try:
            if not self.build_cov_fuzzer():
                return None
            if not self.run_coverage():
                return None
            if not self.merge_profdata():
                return None
            return self.export_coverage()
            
        finally:
            self.docker.remove_image()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Coverage Docker Pipeline")
    parser.add_argument("--project", help="OSS-Fuzz project name", required=True)
    parser.add_argument("--oss-fuzz", default="/home/yk/code/oss-fuzz")

    args = parser.parse_args()
    pipeline = CoverageDocker(
        project=args.project,
        oss_fuzz_dir=Path(args.oss_fuzz),
    )
    
    result = pipeline.run()
    

