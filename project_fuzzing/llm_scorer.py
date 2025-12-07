#!/usr/bin/env python3
"""
Coverage Scorer - Score uncovered functions using LLM for fuzzing value.

Usage:
    python coverage_scorer.py coverage_output/libssh/functions.json
    python coverage_scorer.py functions.json --limit 50 --model gpt-4o-mini
    python coverage_scorer.py functions.json --batch  # Use Batch API (50% cheaper)
    
Output: functions_scored.json with LLM scores
"""

from enum import Enum
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional
import openai
SCORE_PROMPT = """You are an expert in fuzzing and security testing. Analyze this function and assign a score from 0-10 based on its value as a fuzz target.

SCORING CRITERIA:

Score 9-10 (Critical Priority):
- Parses complex external input (file formats, network protocols, serialized data)
- Performs memory operations on untrusted data (memcpy, string ops, buffer manipulation)
- Has high cyclomatic complexity with branching logic
- Handles authentication, cryptography, or security-critical operations

Score 7-8 (High Priority):
- Processes structured data with validation/parsing logic
- Contains loops with external input-dependent bounds
- Performs type conversions or data transformations on user input
- Has multiple code paths that depend on input values

Score 4-6 (Medium Priority):
- Helper functions that process data but with simple logic
- Functions with moderate complexity (cyclomatic 3-10)
- Data structure manipulation with limited external input

Score 1-3 (Low Priority):
- Simple getters, setters, or accessors
- Wrapper functions with minimal logic
- Trivial validation checks

Score 0 (Not Suitable):
- Functions with no external input parameters
- Pure computational functions (math operations on known values)
- Destructors, constructors with no complex logic
- Internal state management without user-controllable data

Return JSON only:
{{"score": <0-10>, "reason": "<brief explanation>"}}

Function: {name}
Source code: {source_code}
"""

class LLMName(Enum):
    GPT4O = "gpt-4o"
    GPT4OMINI = "gpt-4o-mini"
    GPT5 = "gpt-5"
    GPT5MINI = "gpt-5-mini"
    
class CoverageScorer:
    """Score functions using LLM."""
    
    def __init__(self, model: str = "gpt-5-mini"):
        self.model = model
        self.setup_client()

    def setup_client(self):
        """Load or switch LLM model."""
       
        if self.model == LLMName.GPT5MINI.value:
            self.temperature = 1.0
        else:
            self.temperature = 0.1
        try:
            self.client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        except Exception as e:
            print("[!] OpenAI client initialization error:", e)
            return None

    def _parse_score_response(self, content: str) -> dict:
        """Parse score and reason from LLM response JSON.
        
        Args:
            content: Raw LLM response string containing JSON
            
        Returns:
            dict with 'score' and 'reason' keys
        """
        start = content.find('{')
        end = content.rfind('}') + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(content[start:end])
                score = data.get('score', -1)
                if score != -1:
                    return {
                        'score': float(score),
                        'reason': data.get('reason', 'no reason provided')
                    }
            except json.JSONDecodeError:
                pass
        
        print(f"[-] Failed to parse LLM response: {content}")
        return {'score': -1, 'reason': 'Parse error'}
     
    def score(self, func: dict) -> dict:
        """Score using LLM API."""
        prompt = SCORE_PROMPT.format(
            name=func.get('clean_name', func.get('name', '')),
            source_code=func.get('source_code', ''),
        )
        
        for _ in range(3):  # retry up to 3 times
            try:
                resp = self.client.chat.completions.create(  # type: ignore
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                    # max_completion_tokens=100
                )
                content = resp.choices[0].message.content or ""
                result = self._parse_score_response(content)
                
                if result['score'] != -1:
                    func['score'] = result['score']
                    func['reason'] = result['reason']
                    return func
                
            except Exception as e:
                print(f"[!] LLM error for {func.get('name', '?')}: {e}")
            
        func['score'] = -1
        func['reason'] = "LLM scoring failed, pass"
        return func
    
    def score_all_individual(self, functions: list[dict], 
                    limit: int = 100, 
                    delay: float = 0.3) -> list[dict]:
        """Score multiple functions using individual API calls."""
        to_score = functions[:limit]
        
        for i, func in enumerate(to_score):
            self.score(func)
            if (i + 1) % 10 == 0:
                print(f"[*] Scored {i+1}/{len(to_score)}")
            if self.client and delay > 0:
                time.sleep(delay)
        
        # Sort by score descending
        to_score.sort(key=lambda f: f.get('score', 0), reverse=True)
        return to_score

    def submit_batch(self, functions: list[dict], 
                     limit: int = 100,
                     batch_file: Optional[Path] = None) -> str:
        """Submit functions to OpenAI Batch API (50% cheaper).
        
        Returns batch_id for later retrieval.
        """
        to_score = functions[:limit]
        
        # Create JSONL file for batch
        if batch_file is None:
            batch_file = Path(f"batch_input_{int(time.time())}.jsonl")
        
        with open(batch_file, 'w') as f:
            for i, func in enumerate(to_score):
                prompt = SCORE_PROMPT.format(
                    name=func.get('clean_name', func.get('name', '')),
                    source_code=func.get('source_code', ''),
                )
                request = {
                    "custom_id": f"func-{i}",
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": {
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": self.temperature,
                    }
                }
                f.write(json.dumps(request) + "\n")
        
        print(f"[*] Created batch input file: {batch_file}")
        
        # Upload file
        with open(batch_file, 'rb') as f:
            file_obj = self.client.files.create(file=f, purpose="batch") # type: ignore
        
        print(f"[*] Uploaded file: {file_obj.id}")
        
        # Create batch
        batch = self.client.batches.create(  # type: ignore
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={"description": f"Scoring {len(to_score)} functions"}
        )
        
        print(f"[+] Batch submitted: {batch.id}")
        print(f"[*] Status: {batch.status}")
        
        # Save batch info for later retrieval
        batch_info = {
            "batch_id": batch.id,
            "input_file_id": file_obj.id,
            "num_functions": len(to_score),
            "batch_input_file": str(batch_file),
            "created_at": time.time()
        }
        info_file = batch_file.with_suffix('.info.json')
        with open(info_file, 'w') as f:
            json.dump(batch_info, f, indent=2)
        print(f"[*] Batch info saved to: {info_file}")
        
        return batch.id

    def retrieve_batch(self, batch_id: str, 
                       functions: list[dict]) -> list[dict]:
        """Retrieve batch results and apply scores to functions.
        
        Args:
            batch_id: The batch ID from submit_batch
            functions: Original functions list (same order as submitted)
        """

        # Wait for completion if requested
        batch = self.client.batches.retrieve(batch_id) # type: ignore
        print(f"[*] Batch status: {batch.status}")
        
        if batch.status != "completed":
        # batch.status in ("failed", "expired", "cancelled"):
            raise RuntimeError(f"Batch {batch.status}: {batch.errors}")
        
   
        # Download results
        if not batch.output_file_id:
            raise RuntimeError("No output file available")
        
        content = self.client.files.content(batch.output_file_id) # type: ignore
        results = {}
        
        for line in content.text.strip().split('\n'):
            if not line:
                continue
            result = json.loads(line)
            custom_id = result.get('custom_id', '')
            idx = int(custom_id.replace('func-', ''))
            
            response = result.get('response', {})
            if response.get('status_code') == 200:
                body = response.get('body', {})
                choices = body.get('choices', [])
                if choices:
                    content_str = choices[0].get('message', {}).get('content', '')
                    result = self._parse_score_response(content_str)
                    results[idx] = result
        
        # Apply scores to functions
        for i, func in enumerate(functions):
            if i in results:
                func['score'] = results[i]['score']
                func['reason'] = results[i]['reason']
            else:
                func['score'] = -1
                func['reason'] = 'No result'
        
        # Sort by score descending
        functions.sort(key=lambda f: f.get('score', 0), reverse=True)
        
        print(f"[+] Retrieved {len(results)} scores from batch")
        return functions


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Score functions for fuzzing value")
    parser.add_argument("--project", required=True, help="Path to functions.json")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--coverage-percent", type=float, default=30.0)
    
    # Batch API options (50% cheaper)
    parser.add_argument("--batch", action="store_true", help="Use Batch API (50% cheaper, async)")
    parser.add_argument("--batch-id", default="", help="Retrieve results from existing batch")
    
    args = parser.parse_args()
   
    # Load functions
    input_path = Path("/home/yk/code/LLM-reasoning-agents/project_fuzzing/projects") / args.project / "functions.json"
    if not os.path.exists(input_path):
        print(f"[-] File not found: {input_path}")
        sys.exit(1)
    
    with open(input_path) as f:
        data = json.load(f)
    
    functions = data.get('functions', [])
    
    # Filter if needed
    if args.coverage_percent:
        functions = [f for f in functions if f.get('coverage_percent', 0.0) <= args.coverage_percent]
    
    scorer = CoverageScorer(model=args.model)

    # Check batch status only

    # Retrieve existing batch results
    if args.batch_id:
        print(f"[*] Retrieving batch {args.batch_id}...")
        scored = scorer.retrieve_batch(args.batch_id, functions[:args.limit])
    # Submit new batch
    elif args.batch:
        print(f"[*] Submitting batch for {min(args.limit, len(functions))} functions...")
        batch_id = scorer.submit_batch(functions, limit=args.limit)
        print(f"\n[+] Batch submitted! To retrieve results later, run:")
        print(f"    python {sys.argv[0]} --project {args.project} --batch-id {batch_id}")
        return
    # Regular scoring (individual API calls)
    else:
        print(f"[*] Scoring {min(args.limit, len(functions))} functions...")
        scored = scorer.score_all_individual(functions, limit=args.limit)
    
    # Output path
    output_path = Path("/home/yk/code/LLM-reasoning-agents/project_fuzzing/projects") / args.project / "functions_scored.json"
    
    # Save
    output = {
        'project': args.project,
        'model': args.model,
        'total_scored': len(scored),
        'high_value': len([f for f in scored if f.get('score', 0) >= 7]),
        'functions': scored
    }
    
    # if in batch mode, add batch_id in filename
    if args.batch_id:
        output['batch_id'] = args.batch_id
        output_path = output_path.with_name(f"{output_path.stem}_{args.batch_id}{output_path.suffix}")

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"[+] Saved to: {output_path}")
    
    # Print top functions
    print(f"\n{'='*60}")
    print(f"Top 15 High-Value Functions ({args.project})")
    print('='*60)
    for func in scored[:15]:
        score = func.get('score', 0)
        name = func.get('clean_name', func.get('name', '?'))
        reason = func.get('reason', '')[:50]
        print(f"  [{score:.1f}] {name[:40]:<40}")
        print(f"        {reason}")


if __name__ == "__main__":
    main()
