import re
import subprocess
from pathlib import Path
from typing import Optional

# Matches: git clone [--options] <url> [dest]
GIT_CLONE_PATTERN = re.compile(
    r'^\s*RUN\s+git\s+clone\s+(?P<options>(?:--[a-z-]+(?:=\S+|\s+\S+)?\s+|-[bq]\s+\S+\s+|-[bq]\s+)*?)'
    r'(?P<url>(?:https?|git)://[^\s]+)'
    r'(?:\s+(?P<dest>[^\s\\]+))?'
)

def parse_branch_from_options(options: str) -> Optional[str]:
    """Extract branch name from options like '--branch develop'."""
    parts = options.strip().split()
    for i in range(len(parts)):
        if parts[i] in ('--branch', '-b') and i + 1 < len(parts):
            return parts[i + 1]
    return None

def get_latest_commit(git_url: str, branch: Optional[str] = None) -> Optional[str]:
    """Get latest commit from a remote git URL. Use HEAD or specific branch."""
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    try:
        result = subprocess.run(
            ['git', 'ls-remote', git_url, ref],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip().split()[0]
        else:
            print(f"Failed to get commit for {git_url} {ref}:\n{result.stderr.strip()}")
    except Exception as e:
        print(f"Error accessing {git_url}: {e}")
    return None

def remove_depth_option(options: str) -> str:
    """Remove --depth options from git clone command."""
    # Handle both --depth=1 and --depth 1 formats
    options = re.sub(r'--depth=\S+\s*', '', options)
    options = re.sub(r'--depth\s+\S+\s*', '', options)
    return options

def process_dockerfile(dockerfile_path: Path):
    flag_pattern = "# freeze Sep 9"
    content = dockerfile_path.read_text()
    original_lines = content.splitlines()

    if flag_pattern in original_lines and "# freeze Aug 4" in original_lines:
        print(f"Skipping {dockerfile_path}, already processed.")
        return
    
    if flag_pattern in original_lines or "# freeze Aug 4" in original_lines:
        # print(f"Skipping {dockerfile_path}, already processed.")
        return
    
    if "git clone" not in content:
        # print(f"No git clone found in {dockerfile_path}, skipping.")
        return
    new_lines: list[str] = []

    total_match = 0
    for line in original_lines:
        match = GIT_CLONE_PATTERN.search(line)
        
        if match:
            options = match.group("options") or ""
            # Remove --depth option to ensure full history is cloned
            options = remove_depth_option(options)
            git_url = match.group("url")
            dest_dir = match.group("dest")
            if dest_dir == "&&" or dest_dir is None:
                dest_dir = git_url.rstrip('/').split('/')[-1].replace('.git', '')
            branch = parse_branch_from_options(options)
            commit = get_latest_commit(git_url, branch)

            total_match += 1
            if commit:
                new_line = (
                    f'RUN git clone {options}{git_url} {dest_dir} && \\\n'
                    f'    cd {dest_dir} && \\\n'
                    f'    git checkout {commit}'
                )
                if "&&" in line:
                    new_line+= " && \\\n"
                    # back to original dir
                    new_line += "    cd - && \\\n"
                    # keep the rest
                    new_line += " && ".join(line.split("&&")[1:])

                # print(f"{git_url} pinned to {commit}")
                new_lines.append(new_line)
                continue
            else:
                print(f"Could not pin for {dockerfile_path}, keeping original line.")
        new_lines.append(line)

    new_lines.append(flag_pattern)
    if total_match >= 1:
        dockerfile_path.write_text('\n'.join(new_lines) + '\n')
    # elif total_match > 1:
        # print(f"Warning: Multiple git clone commands found in {dockerfile_path}. Please check manually.")
    else:
        print(f"No match for git clone commands found in {dockerfile_path}. No changes made.")

def scan_for_dockerfiles(root_dir: str):
    for dockerfile in Path(root_dir).rglob('Dockerfile'):
        process_dockerfile(dockerfile)

def extract_all_projects(root_dir: Path) -> list[str]:
    projects: list[str] = []
    for project_yaml in root_dir.iterdir():
        if project_yaml.is_file() and project_yaml.name.endswith('.yaml'):
            project_name = project_yaml.stem
            projects.append(project_name)
    return projects

def freeze_base_image(dockerfile_path: Path):

    base_str = "FROM gcr.io/oss-fuzz-base/base-builder"
    clang_hash = "@sha256:d34b94e3cf868e49d2928c76ddba41fd4154907a1a381b3a263fafffb7c3dce0"
    content = dockerfile_path.read_text()
    lines = content.splitlines()

    new_lines: list[str] = []
    count = 0
    for line in lines:
        if line.strip() == base_str:
            line = base_str + clang_hash
            count += 1
        new_lines.append(line)

    dockerfile_path.write_text('\n'.join(new_lines) + '\n')
    if count != 1:
        print(f"Warning: base image line not found or multiple found in {dockerfile_path}.")
        return
    
if __name__ == '__main__':

    bench_dir = Path("/home/yk/code/LLM-reasoning-agents/benchmark-sets/all")
    project_list = extract_all_projects(bench_dir)
    oss_fuzz_dir = Path("/home/yk/code/oss-fuzz/projects")
    for project in project_list:
        # if project in ["bind9", "gdk-pixbuf", "libzip", "civetweb", "inchi", "igraph"]:
            # continue
        # if project != "bind9":
            # continue
        project_path = oss_fuzz_dir / project / "Dockerfile"
        freeze_base_image(project_path)
        # exit()
