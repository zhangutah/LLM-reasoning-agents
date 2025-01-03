import time
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Dict, Any


class EclipseJdtLsClient:
    def __init__(self, server_args, workspace_path):
        self.server_args = server_args
        self.workspace_path = workspace_path
        self.server_process = None
        self.reader = None
        self.writer = None
        self.message_id = 0
        self.pending_requests = {}

    async def start_server(self):
        """Start the Java LSP server."""
        self.server_process = await asyncio.create_subprocess_exec(
            *self.server_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.reader = self.server_process.stdout
        self.writer = self.server_process.stdin
        asyncio.create_task(self._listen_to_server())

    async def _listen_to_server(self):
        """Listen to messages from the server."""
        while True:
            try:
                # Read the content length header
                header = await self.reader.readline()
                if not header:
                    break
                content_length = int(header.decode().strip().split(": ")[1])

                # Read the blank line
                await self.reader.readline()

                # Read the actual JSON-RPC message
                content = await self.reader.read(content_length)
                message = json.loads(content.decode())

                # Handle responses and notifications
                if "id" in message and message["id"] in self.pending_requests:
                    future = self.pending_requests.pop(message["id"])
                    future.set_result(message)
                elif "method" in message and message["method"] == 'window/logMessage' and ">> registerWatchers" in message["params"].get('message'):
                    future = self.pending_requests.pop(65)
                    future.set_result(message)
                else:
                    self._handle_notification(message)
            except Exception as e:
                print(f"Error reading from server: {e}")
                break

    def _handle_notification(self, message: Dict[str, Any]):
        """Handle notifications from the server (e.g., logs, diagnostics)."""
        print(f"Notification from server: {message}")

    async def send_request(self, method: str, params: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """Send a JSON-RPC request to the server."""
        self.message_id = self.message_id + 1 if method != "initialized" else 65
        request = {
            "jsonrpc": "2.0",
            "id": self.message_id,
            "method": method,
            "params": params,
        }
        request_str = json.dumps(request)
        content_length = len(request_str)
        self.writer.write(f"Content-Length: {content_length}\r\n\r\n".encode())
        self.writer.write(request_str.encode())
        await self.writer.drain()

        # Wait for the response
        future = asyncio.get_event_loop().create_future()
        self.pending_requests[self.message_id] = future

        try:
            # Wait for the response with a timeout
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            print(f"Request '{method}' timed out after {timeout} seconds.")
            # Return an empty dictionary on timeout
            return {}
        finally:
            # Clean up the pending request if it timed out
            if self.message_id in self.pending_requests:
                del self.pending_requests[self.message_id]

    async def initialize(self):
        """Send the initialize request."""
        params = {
            "processId": None,
            "rootUri": f"file://{self.workspace_path}",
            "capabilities": {
                    'textDocument': {
                         'definition': {'dynamicRegistration': True},
                         'references': {'dynamicRegistration': True}
                }
            },
        }
        response = await self.send_request("initialize", params)
        print(f"Initialize response: {response}")
        await self.send_request("initialized", {})  # Notify server that initialization is complete

    async def open_file(self, file_path: str):
        """Send a textDocument/didOpen notification to open a file."""
        file_uri = f"file://{file_path}"
        with open(file_path, "r") as f:
            text = f.read()

        params = {
            "textDocument": {
                "uri": file_uri,
                "languageId": "java",
                "version": 1,
                "text": text,
            }
        }
        await self.send_request("textDocument/didOpen", params)

    async def find_definition(self, file_path: str, line: int, character: int):
        """Send a textDocument/definition request."""
        file_uri = f"file://{file_path}"
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self.send_request("textDocument/definition", params)
        print(f"Definition response: {response}")
        return response

    async def find_references(self, file_path: str, line: int, character: int):
        """Send a textDocument/references request."""
        file_uri = f"file://{file_path}"
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        }
        response = await self.send_request("textDocument/references", params)
        print(f"References response: {response}")
        return response

    async def stop_server(self):
        """Stop the Java LSP server."""
        if self.server_process:
            self.server_process.terminate()
            await self.server_process.wait()


async def main():
    # Define server arguments
    server_args = [
        "java",
        "-Declipse.application=org.eclipse.jdt.ls.core.id1",
        "-Dosgi.bundles.defaultStartLevel=4",
        "-Declipse.product=org.eclipse.jdt.ls.core.product",
        "-Dlog.level=ALL",
        "-Xmx1G",
        "--add-modules=ALL-SYSTEM",
        "--add-opens",
        "java.base/java.util=ALL-UNNAMED",
        "--add-opens",
        "java.base/java.lang=ALL-UNNAMED",
        "-jar",
        "/home/mlc/packages/examples/resources/eclipse.jdt.ls/ls/plugins/org.eclipse.equinox.launcher_1.6.900.v20240613-2009.jar",
        "-configuration",
        "/home/mlc/packages/examples/resources/eclipse.jdt.ls/ls/config_linux",
        "-data",
        "/home/mlc/packages/examples/resources/eclipse.jdt.ls/workspace",
    ]

    # Workspace path and target Java file
    workspace_path = "/home/mlc/packages/examples/resources/eclipse.jdt.ls/workspace"
    file_path = f"{workspace_path}/QuickSortExample.java"

    # Initialize the client
    client = EclipseJdtLsClient(server_args, workspace_path)
    await client.start_server()
    await client.initialize()

    # Open the Java file
    print("TEST open_file")
    await client.open_file(file_path)

    print("TEST find_definition")
    # Find definition (example: line 1, character 5)
    await client.find_definition(file_path, line=25, character=22)

    print("TEST find_references")
    # Find references (example: line 1, character 5)
    await client.find_references(file_path, line=23, character=24)

    # Stop the server
    await client.stop_server()


if __name__ == "__main__":
    asyncio.run(main())

