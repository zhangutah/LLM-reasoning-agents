import logging
import random
import time
from typing import Any, Dict, List, Optional, OrderedDict, TypeVar
from urllib.parse import urlencode
import requests
import json
import yaml
import os
import re
from pathlib import Path
from utils.misc import extract_name


logger = logging.getLogger(__name__)

TIMEOUT = 45
MAX_RETRY = 5


def _construct_url(api: str, params: dict) -> str:
	"""Constructs an encoded url for the |api| with |params|."""
	return api + '?' + urlencode(params)


def query_introspector(api: str, params: dict) -> Optional[requests.Response]:
	"""Queries FuzzIntrospector API and returns the json payload,
	returns an empty dict if unable to get data."""
	for attempt_num in range(1, MAX_RETRY + 1):
		try:
			resp = requests.get(api, params, timeout=TIMEOUT)
			if not resp.ok:
				print(
					'Failed to get data from FI:\n'
					'%s\n'
					'-----------Response received------------\n'
					'%s\n'
					'------------End of response-------------', resp.url,
					resp.content.decode('utf-8').strip())
				break
			return resp
		except requests.exceptions.Timeout as err:
			if attempt_num == MAX_RETRY:
				print(
					'Failed to get data from FI due to timeout, max retry exceeded:\n'
					'%s\n'
					'Error: %s', _construct_url(api, params), err)
				break
			delay = 5 * 2**attempt_num + random.randint(1, 10)
			logger.warning(
				'Failed to get data from FI due to timeout on attempt %d:\n'
				'%s\n'
				'retry in %ds...', attempt_num, _construct_url(api, params), delay)
			time.sleep(delay)
		except requests.exceptions.RequestException as err:
			print(
				'Failed to get data from FI due to unexpected error:\n'
				'%s\n'
				'Error: %s', _construct_url(api, params), err)
			break

	return None



def build_ntu_bench(bench_dir: str) -> None:
	INTROSPECTOR_ENDPOINT = 'https://introspector.oss-fuzz.com/api'
	INTROSPECTOR_FUNC_SIG = f'{INTROSPECTOR_ENDPOINT}/function-signature'

	bench_path = Path(bench_dir)
	function_list: dict[str, list[str]] = {}

	for project_path in bench_path.iterdir():

		# if project_path.stem != "gpac":
			# continue

		if not project_path.is_file():
			continue
		if not project_path.suffix == '.yaml':
			continue

		# open yaml file
		with open(project_path, 'r') as f:
			# yaml.safe_load(f)
			project_data = yaml.safe_load(f)
			# project = json.loads(line)
			project_name = project_data['project']

			function_list[project_name] = []
			for function_info in project_data['functions']:
				function_list[project_name].append(function_info["name"])

    # "functions":
    # - "name": "_ZZN4absl19str_format_internal12_GLOBAL__N_124FractionalDigitGenerator13RunConversionENS_7uint128EiNS_11FunctionRefIFvS2_EEEENKUlNS_4SpanIjEEE_clES8_"
    #   "params":
    #   - "name": "this"
    #     "type": "bool "
    #   - "name": "input"
    #     "type": "bool "
    #   - "name": ""
    #     "type": "size_t"
    #   "return_type": "void"
    #   "signature": "void absl::str_format_internal::(anonymous namespace)::FractionalDigitGenerator::operator()(const void *, Span<unsigned int>)"

	for project_name in function_list.keys():
		
		# if project_name == "spdk":
			# print(f"Skipping {project_name}, number of functions: {len(function_list[project_name])}")
			# continue


		# create a yaml file for each
		project_yaml: dict[str, Any] = {"functions": [],
						"project": project_name}

		# oss benchmark yaml path
		oss_yaml_path = f'/home/yk/code/fuzz-introspector/scripts/oss-fuzz-gen-e2e/workdir/oss-fuzz-gen/benchmark-sets/all/{project_name}.yaml'
		if not os.path.exists(oss_yaml_path):
			# print(f"File {oss_yaml_path} does not exist")

			target_path_mapping = {
				"md4c": " /src/md4c/test/fuzzers/fuzz-mdhtml.c",
				# "spdk": "/src/spdk/test/fuzz/fuzz_bdev.c",
				"gdk-pixbuf": "/src/fuzz/stream_fuzzer.c",
				"libucl": "/src/libucl/tests/fuzzers/ucl_add_string_fuzzer.c",
				"inchi": "/src/inchi_input_fuzzer.c"
			}
			target_name_mapping = {
				"md4c": "fuzz-mdhtml",
				"gdk-pixbuf": "stream_fuzzer",
				"libucl": "ucl_add_string_fuzzer",
				"inchi": "inchi_input_fuzzer"
			}
			
			project_yaml['target_path'] = target_path_mapping[project_name]
			project_yaml['language'] = "c"
			project_yaml['target_name'] = target_name_mapping[project_name]
		else:
			# read oss fuzz gen yaml  to obtain the above information
			with open(oss_yaml_path, 'r') as f:
				oss_fuzz_gen = yaml.safe_load(f)
				project_yaml['target_path'] = oss_fuzz_gen['target_path']
				project_yaml['language'] = oss_fuzz_gen['language']
				project_yaml['target_name'] = oss_fuzz_gen['target_name']

		for function in function_list[project_name]:

			query_params = {
				'project': project_name,
				'function': function
			}
	
			response = query_introspector(INTROSPECTOR_FUNC_SIG, query_params)

			if response is None:
				print(f"Failed to get function signature for {function}")
				continue
				
			func_data = json.loads(response.text)
			if func_data["result"] != "success" or not func_data["raw_data"]:
				print(f"Project name:{project_name}, Failed to get function signature for {function}")
				continue

			params: list[dict[str, str]] = []
			for function_type_list in func_data["raw_data"]["func_signature_elems"]["params"]:
				params.append(
					{
						"name": "this",
						"type": function_type_list[-1]
					}
				)

			project_yaml['functions'].append(
				{
					"name": function,
					"params": params,
					"return_type": func_data["raw_data"]["return_type"],
					"signature": func_data['signature']
				}
			)

		os.makedirs(f'/home/yk/code/fuzz-introspector/scripts/oss-fuzz-gen-e2e/workdir/oss-fuzz-gen/benchmark-sets/ntu', exist_ok=True)

		# write to yaml file
		with open(f'/home/yk/code/fuzz-introspector/scripts/oss-fuzz-gen-e2e/workdir/oss-fuzz-gen/benchmark-sets/ntu/{project_name}.yaml', 'w') as f:
			yaml.dump(project_yaml, f, default_flow_style=False, width=float("inf"), allow_unicode=True)  


def build_bench(text_file: str, project_name: str) -> None:
	INTROSPECTOR_ENDPOINT = 'https://introspector.oss-fuzz.com/api'
	INTROSPECTOR_FUNC_SIG = f'{INTROSPECTOR_ENDPOINT}/function-signature'

	function_list: List[str] = []
	with open(text_file, 'r') as f:
		for line in f.readlines():
			sig = line.strip()
			# if sig.startswith("Hun"):
			sig = "void " + sig
			function_list.append(sig)
	# create a yaml file for each
	project_yaml: dict[str, Any] = {"functions": [],
					"project": project_name}

	# oss benchmark yaml path
	oss_yaml_path = f'/home/yk/code/fuzz-introspector/scripts/oss-fuzz-gen-e2e/workdir/oss-fuzz-gen/benchmark-sets/all/{project_name}.yaml'
	if not os.path.exists(oss_yaml_path):
		print(f"File {oss_yaml_path} does not exist")
	else:
		# read oss fuzz gen yaml  to obtain the above information
		with open(oss_yaml_path, 'r') as f:
			oss_fuzz_gen = yaml.safe_load(f)
			project_yaml['target_path'] = oss_fuzz_gen['target_path']
			project_yaml['language'] = oss_fuzz_gen['language']
			project_yaml['target_name'] = oss_fuzz_gen['target_name']

	for function in function_list:

		
		project_yaml['functions'].append(
			{
				"name": extract_name(function),
				"params": [],
				"return_type": "void",
				"signature": function
			}
		)

	os.makedirs(f'/home/yk/code/LLM-reasoning-agents/benchmark-sets/yunhang/', exist_ok=True)

	# write to yaml file
	with open(f'/home/yk/code/LLM-reasoning-agents/benchmark-sets/yunhang/{project_name}.yaml', 'w') as f:
		yaml.dump(project_yaml, f, default_flow_style=False, width=float("inf"), allow_unicode=True)

build_bench("/home/yk/code/LLM-reasoning-agents/utils/hunspell.txt", "hunspell")