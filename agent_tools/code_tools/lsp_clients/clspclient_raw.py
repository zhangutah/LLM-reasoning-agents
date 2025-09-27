import asyncio
import json
from pathlib import Path
from typing import Dict, Any


class ClangdLspClient:
    def __init__(self, workspace_path, language="c"):
        self.workspace_path = workspace_path
        self.language = language
        self.server_process = None
        self.reader = None
        self.writer = None
        self.message_id = 0
        self.pending_requests = {}

    async def start_server(self):
        """Start the clangd LSP server."""
        self.server_process = await asyncio.create_subprocess_exec(
            "clangd-18",
            # "--resource-dir=/usr/local/lib/clang/18",
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
                header_str = header.decode().strip()
                if not header_str.startswith("Content-Length:"):
                    print(f"Unexpected header: {header_str}")
                    continue
                content_length = int(header_str.split(": ")[1])

                # Read the blank line
                await self.reader.readline()

                # Read the actual JSON-RPC message
                content = b""
                bytes_to_read = content_length
                while bytes_to_read > 0:
                    chunk = await self.reader.read(min(bytes_to_read, 8192))
                    if not chunk:
                        print(f"Connection closed while reading content. Expected {content_length} bytes, got {len(content)}")
                        break
                    content += chunk
                    bytes_to_read -= len(chunk)
                
                if len(content) != content_length:
                    print(f"Content length mismatch: expected {content_length}, got {len(content)}")
                    continue
                try:
                    decoded_content = content.decode()
                    message = json.loads(decoded_content)
                except json.JSONDecodeError as json_err:
                    print(f"JSON decode error: {json_err}")
                    print(f"Content length: {content_length}, Actual content length: {len(content)}")
                    print(f"Content preview (first 200 chars): {content[:200]}")
                    print(f"Content preview (last 200 chars): {content[-200:]}")
                    # Try to find where the JSON becomes invalid
                    decoded_content = content.decode(errors='replace')
                    if len(decoded_content) > 4000:
                        print(f"Content around error position: {decoded_content[4000:4100]}")
                    
                    # Try to recover by finding complete JSON objects
                    try:
                        # Attempt to fix unterminated strings by adding closing quotes
                        fixed_content = self._attempt_json_recovery(decoded_content)
                        if fixed_content:
                            message = json.loads(fixed_content)
                            print("Successfully recovered malformed JSON")
                        else:
                            continue
                    except Exception as recovery_err:
                        print(f"JSON recovery failed: {recovery_err}")
                        continue
                except UnicodeDecodeError as decode_err:
                    print(f"Unicode decode error: {decode_err}")
                    print(f"Content length: {content_length}, Actual content length: {len(content)}")
                    continue

                # Handle responses and notifications
                if "id" in message and message["id"] in self.pending_requests:
                    future = self.pending_requests.pop(message["id"])
                    future.set_result(message)
                # elif "method" in message and message["method"] == 'window/logMessage' and ">> registerWatchers" in message["params"].get('message'):
                #     future = self.pending_requests.pop(65)
                #     future.set_result(message)
                else:
                    self._handle_notification(message)
            except Exception as e:
                print(f"Error reading from server: {e}")
                break

    def _attempt_json_recovery(self, content: str) -> str:
        """Attempt to recover malformed JSON by fixing common issues."""
        try:
            # First, try to find the last complete JSON object
            brace_count = 0
            last_complete_pos = -1
            in_string = False
            escape_next = False
            
            for i, char in enumerate(content):
                if escape_next:
                    escape_next = False
                    continue
                    
                if char == '\\':
                    escape_next = True
                    continue
                    
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                    
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            last_complete_pos = i + 1
            
            if last_complete_pos > 0:
                return content[:last_complete_pos]
                
            # If no complete object found, try to close unterminated strings
            if content.count('"') % 2 != 0:
                # Find the last quote and add a closing quote
                last_quote = content.rfind('"')
                if last_quote > 0:
                    # Check if this quote is escaped
                    escaped = False
                    check_pos = last_quote - 1
                    while check_pos >= 0 and content[check_pos] == '\\':
                        escaped = not escaped
                        check_pos -= 1
                    
                    if not escaped:
                        # Add closing quote and brace
                        return content[:last_quote + 1] + '"}'
                        
        except Exception as e:
            print(f"Error in JSON recovery: {e}")
            
        return ""

    def _handle_notification(self, message: Dict[str, Any]):
        """Handle notifications from the server (e.g., logs, diagnostics)."""
        print(f"Notification from server: {message}")

    async def send_request(
        self, method: str, params: Dict[str, Any], timeout: float = 0.1
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request to the server."""
        if method in ("textDocument/didOpen", "initialized"):
            self.message_id = 0
            request = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        else:
            self.message_id = self.message_id + 1
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
            response = await asyncio.wait_for(future, timeout)
            return response
        except asyncio.TimeoutError:
            print(f"Request '{method}' with params {params} timed out after {timeout} seconds.")
            # Clean up pending request
            if self.message_id in self.pending_requests:
                del self.pending_requests[self.message_id]
            return {}
        except Exception as e:
            print(f"Error in request '{method}': {e}")
            # Clean up pending request
            if self.message_id in self.pending_requests:
                del self.pending_requests[self.message_id]
            return {}

    async def initialize(self):
        """Send the initialize request."""
        params = {
            "processId": None,
            "rootUri": f"file://{self.workspace_path}",
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": True},
                    "declaration": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "callHierarchy": {"dynamicRegistration": True},
                    "typeDefinition": {"dynamicRegistration": True},
                },
                "workspace": {
                      "symbol":{"dynamicRegistration": True},
                }

            }
        }
        response = await self.send_request("initialize", params)
        print(f"Initialize response: {response}")
        await self.send_request("initialized", {})

    async def find_declaration(self, file_path: str, line: int, character: int):
        """Send a textDocument/declaration request."""
        file_uri = Path(file_path).as_uri()
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self.send_request("textDocument/declaration", params)
        if "error" in response:
            print(f"Error finding declaration: {response['error']}")
            print(f"File: {file_path}")
            print(f"Position: line {line}, character {character}")
        else:
            print(f"Declaration response: {response}")
        return response

    async def find_workspace_symbols(self, symbol_name: str = ""):
        """Send a workspace/symbol request to find all symbols in the workspace."""
        params = {
            "query": symbol_name,  # Empty query returns all symbols
        }
        response = await self.send_request("workspace/symbol", params, timeout=5)
        if "error" in response:
            print(f"Error finding workspace symbols: {response['error']}")
        else:
            print(f"Workspace symbols response: {response}")
        return response

    async def open_file(self, file_path: str):
        """Send a textDocument/didOpen notification to open a file."""
        file_uri = Path(file_path).as_uri()
        with open(file_path, "r") as f:
            text = f.read()

        params = {
            "textDocument": {
                "uri": file_uri,
                "languageId": self.language,  # Changed from "java" to "c"
                "version": 1,
                "text": text,
            }
        }
        response = await self.send_request("textDocument/didOpen", params)
        print(f"Open-file response: {response}")

    async def find_definition(self, file_path: str, line: int, character: int):
        """Send a textDocument/definition request."""
        file_uri = Path(file_path).as_uri()
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        response = await self.send_request("textDocument/definition", params)
        print(f"Definition response: {response}")
        return response

    async def find_references(self, file_path: str, line: int, character: int):
        """Send a textDocument/references request."""
        file_uri = Path(file_path).as_uri()
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        }
        response = await self.send_request("textDocument/references", params)
        print(f"References response: {response}")
        return response

    async def stop_server(self):
        """Stop the clangd LSP server."""
        if self.server_process:
            # First try to terminate gracefully
            self.server_process.terminate()
            try:
                # Wait for graceful shutdown with timeout
                await asyncio.wait_for(self.server_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                # If graceful shutdown fails, force kill
                self.server_process.kill()
                try:
                    await asyncio.wait_for(self.server_process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Process is really stuck, might be a zombie
                    pass

    async def wait_for_indexing(self, timeout=5):
        """Wait for clangd to finish indexing."""
        # Sleep a bit to allow indexing to start/complete
        await asyncio.sleep(timeout)

    def format_location_response(self, response: Dict[str, Any]) -> str:
        """Format location response for better readability."""
        if "error" in response:
            return f"Error: {response['error']['message']}"

        if "result" not in response or not response["result"]:
            return "No results found"

        result = response["result"]
        if isinstance(result, list):
            locations = result
        else:
            locations = [result]

        formatted_results = []
        for loc in locations:
            if isinstance(loc, dict):
                uri = loc.get("uri", "").replace("file://", "")
                range_info = loc.get("range", {})
                start = range_info.get("start", {})
                line = start.get("line", 0) + 1  # Convert to 1-based line number
                character = (
                    start.get("character", 0) + 1
                )  # Convert to 1-based character
                formatted_results.append(
                    f"File: {uri}, Line: {line}, Column: {character}"
                )

        return "\n".join(formatted_results)


async def main():
    workspace_path = "/src/bind9"

    # you have to open a file 
    definition_file = f"{workspace_path}/lib/dns/ds.c"
    # reference_file = f"{workspace_path}/tools/tiffdither.c"

    client = ClangdLspClient(workspace_path, language="c++")
    await client.start_server()
    await client.initialize()

    print("Opening files...")
    await client.open_file(definition_file)
    # await client.open_file(reference_file)
    
    print("Waiting for clangd to index files...")
    await client.wait_for_indexing()

    all_symbol = await client.find_workspace_symbols("dns_name_fromwire")
    print("All symbols:", all_symbol)   

    exit()
    # Find declaration
    print("\nFinding declaration...")
    declaration_response = await client.find_declaration(
        reference_file,
        line=275,  # 933 - 1 (0-based)
        character=8
    )
    print("Declaration location:")
    print(client.format_location_response(declaration_response))

    # Find definition
    print("\nFinding definition...")
    definition_response = await client.find_definition(
        reference_file,
        line=275,
        character=8
    )
    print("Definition location:")
    print(client.format_location_response(definition_response))

    # Find references
    print("\nFinding references...")
    references_response = await client.find_references(
        reference_file,
        line=275,  # Adjust based on actual definition location
        character=8  # Adjust based on actual definition location
    )
    print("Reference locations:")
    print(client.format_location_response(references_response))

    await client.stop_server()

if __name__ == "__main__":
    asyncio.run(main())