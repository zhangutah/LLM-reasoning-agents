import docker
import subprocess as sp
import os
from constants import LanguageType, DockerResults, PROJECT_PATH
from pathlib import Path
from typing import Union, Optional, Any
import threading

# c++  # cpp for tree-sitter
# go
# rust
# python
# jvm (Java, Kotlin, Scala and other JVM-based languages)
# swift
# javascript
# Fuzzing languages

class DockerUtils:

    def __init__(self, ossfuzz_dir:Path, project_name: str, new_project_name: str, project_lang: LanguageType):
        self.image_name = "gcr.io/oss-fuzz/{}".format(new_project_name)
        self.ossfuzz_dir = ossfuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.project_lang = project_lang

        # for CPP, the language is c++, other languages are the same as the project language
        self.fuzzing_lang = project_lang.value.lower() if project_lang !=  LanguageType.CPP else "c++"


    def build_image(self, build_image_cmd: list[str]) -> bool:
        '''Build the image for the project'''
        try:
            sp.run(build_image_cmd, stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.STDOUT, check=True, timeout=1200, start_new_session=True)
            # sp.run(build_image_cmd, stdin=sp.DEVNULL, stdout=None, stderr=None, check=True, timeout=600)
            return True
        except sp.CalledProcessError as e:
            return False
        except sp.TimeoutExpired as e:
            return False
        except Exception as e:
            print(f"Error building image: {e}")
            return False

    def build_fuzzers(self, build_fuzzer_cmd: list[str]) -> bool:

        # run the build command
        try:
            # self.docker_tool.run_cmd(["find", "-name", "comp"])
            sp.run(build_fuzzer_cmd,
                   stdout=sp.PIPE,  # Capture standard output
                    # Important!, build fuzzer error may not appear in stderr, so redirect stderr to stdout
                   stderr=sp.STDOUT,  # Redirect standard error to standard output
                   text=True,  # Get output as text (str) instead of bytes
                   check=True) # Raise exception if build fails
            return True
        except sp.CalledProcessError as e:
            return False
        except sp.TimeoutExpired as e:
            return False
        except Exception as e:
            print(f"Error building fuzzers: {e}")
            return False


    def remove_image(self) -> str:
        """
        Remove the Docker image from the local machine.
        """
        try:
            client = docker.from_env()
            client.images.remove(self.image_name) # type: ignore
            return "Image removed successfully."
        except docker.errors.ImageNotFound: # type: ignore
            return "Image not found."       
        except Exception as e:
            print(f"Error removing image: {e}")
            return str(e)

    def clean_build_dir(self) -> None:
        """
        Clean the /out directory in the Docker container.
        """
        compile_out_path = os.path.join(self.ossfuzz_dir, "build", "out", self.new_project_name)
        self.run_cmd(["rm", "-rf", "/out/*"], volumes={compile_out_path: {"bind": "/out", "mode": "rw"}})
        self.run_cmd(["rm", "-rf", "/work/*"])


    def run_cmd(self, cmd_list: Union[list[str], str], timeout:int=120, **kargs:Any) -> str:

        # The client timeout should be longer than the container wait timeout
        client = docker.from_env(timeout=timeout)
        container = None
        try:
            container = client.containers.run( # type: ignore
                self.image_name,
                command=cmd_list,  # Simulating a long-running process
                detach=True,
                tty=True, 
                privileged=True,  # Enables privileged mode
                environment={"FUZZING_LANGUAGE": self.fuzzing_lang},  # Set the environment variable
                **kargs
            )

            # Wait for the container to exit, with a timeout
            container.wait(timeout=timeout)  # Timeout in seconds
            logs = container.logs().decode('utf-8') 
            return logs

        except Exception as e:
            # This will catch the timeout from container.wait() and other exceptions
            if container:
                try:
                    container.kill() # type: ignore
                except Exception:
                    pass # Ignore error if container is already stopping/stopped
            return f"{DockerResults.Error.value}: {e}"
        finally:
            # Ensure container is always removed
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass # Ignore error if container was already removed

    def exec_in_container(self, container_id: str, cmd: Union[list[str], str], workdir: Optional[str] = None, timeout: Optional[int] = 60) -> str:
        """
        Execute a command inside a running Docker container with an optional timeout.
        :param container_id: The ID or name of the running container.
        :param cmd: The command to execute (str or list).
        :param workdir: The working directory inside the container (optional).
        :param timeout: Timeout in seconds (optional).
        :return: The command output as a string.
        """
        client = docker.from_env()
        container = client.containers.get(container_id)
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        exec_kwargs: dict[str, Any] = {"cmd": cmd, "stdout": True, "stderr": True, "tty": True, "privileged": True}
        if workdir:
            exec_kwargs["workdir"] = workdir

        result = {"output": ""}

        def run_exec():
            try:
                exec_result = container.exec_run(**exec_kwargs) # type: ignore
                output = exec_result.output.decode("utf-8") if hasattr(exec_result, "output") else exec_result[1].decode("utf-8")
                result["output"] = output
            except Exception as e:
                result["output"] = f"{DockerResults.Error.value}: {str(e)}"

        thread = threading.Thread(target=run_exec)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            # Timeout occurred
            try:
                container.kill()  # type: ignore
                container.start()  # Restart the container if needed
            except Exception:
                pass
            return f"{DockerResults.Error.value}: Command timed out after {timeout} seconds."
        return result["output"]

    def start_container(self, timeout: int=600) -> str:
        """
        Start a Docker container from the image and return its container ID.
        If the container is already running, reuse it.
        """
        try:

            workdir = self.run_cmd(["pwd"], timeout=timeout, volumes=None).strip()
            if workdir.startswith(DockerResults.Error.value):
                # Propagate the error from run_cmd
                return workdir
            client = docker.from_env(timeout=timeout)
            # You can use a unique name for the container to avoid duplicates
            container_name = f"{self.new_project_name}_retriever"
            # Check if container exists and is running
            try:
                container = client.containers.get(container_name)
                if container.status != "running":
                    container.start()
                return container.id # type: ignore
            except Exception as e:
                pass  # Container does not exist, create it

            compile_out_path = str(self.ossfuzz_dir / "build" / "out" / self.new_project_name)
            os.makedirs(compile_out_path, exist_ok=True)
            # You can add more volumes as needed
            container = client.containers.run(
                self.image_name,
                command="/bin/sh",
                name=container_name,
                tty=True,
                detach=True,
                privileged=True,
                environment={"FUZZING_LANGUAGE": self.fuzzing_lang},
                volumes={compile_out_path: {"bind": "/out", "mode": "rw"},
                            os.path.join(PROJECT_PATH, "agent_tools"): {"bind": os.path.join(workdir, "agent_tools"), "mode": "ro"},
                            os.path.join(PROJECT_PATH, "constants.py"): {"bind": os.path.join(workdir, "constants.py"), "mode": "ro"},
                         },
            )
            return container.id # type: ignore
        except Exception as e:
            print(f"Error starting container: {e}")
            return f"{DockerResults.Error.value}: {e}"

    def remove_container(self, container_id: str) -> None:
        """
        Stop and remove a running Docker container by its ID.
        """
        client = docker.from_env()
        try:
            container = client.containers.get(container_id)
            container.stop()
            container.remove()
        except Exception as e:
            print(f"Error stopping/removing container: {e}")
