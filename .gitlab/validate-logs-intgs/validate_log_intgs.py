"""Python script to parse the logs pipeline from the logs-backend repository.
This script is expected to run from a CLI, do not import it."""
import sys
import json
from typing import List, Optional, Set
import re
import yaml
import os

LOGS_BACKEND_INTGS_ROOT = os.path.abspath(os.environ['LOGS_BACKEND_INTGS_ROOT'])
INTEGRATIONS_CORE = os.path.abspath(os.environ['INTEGRATIONS_CORE_ROOT'])

ERR_UNEXPECTED_LOG_COLLECTION_CAT = "The check does not have a log pipeline but defines 'log collection' in its manifest file."
ERR_UNEXPECTED_LOG_DOC = "The check does not have a log pipeline but defines a source in its README."
ERR_MISSING_LOG_COLLECTION_CAT = "The check has a log pipeline called but does not define 'log collection' in its manifest file."
ERR_MISSING_LOG_DOC = "The check has a log pipeline called but does not document log collection in the README file."
ERR_MULTIPLE_SOURCES = "The check has a log pipeline called but documents multiple sources as part of its README file."

EXCEPTIONS = {
    'cilium': [
        ERR_UNEXPECTED_LOG_COLLECTION_CAT,  # cilium does not need a pipeline to automatically parse the logs
        ERR_UNEXPECTED_LOG_DOC  # The documentation says to use 'source: cilium'
    ],
    'amazon_eks': [ERR_UNEXPECTED_LOG_COLLECTION_CAT], # eks is just a tile
    'eks_fargate': [ERR_UNEXPECTED_LOG_COLLECTION_CAT], # Log collection but not from the agent
    'fluentd': [ERR_UNEXPECTED_LOG_COLLECTION_CAT],  # Fluentd is about log collection but we don't collect fluentd logs
    'kubernetes': [ERR_UNEXPECTED_LOG_COLLECTION_CAT],  # The agent collects logs from kubernetes environment but there is no pipeline per se
    'win32_event_log': [ERR_UNEXPECTED_LOG_COLLECTION_CAT],  # win32_event_log is about log collection but we don't collect fluentd logs

}


class CheckDefinition(object):
    def __init__(self, dir_name: str) -> None:
        # Name of the directory for this check in the integrations-core repo.
        self.dir_name = dir_name
        with open(os.path.join(INTEGRATIONS_CORE, dir_name, "manifest.json"), 'r') as manifest:
            content = json.load(manifest)
            # name of the integration
            self.name: str = content['name']
            # id of the integration
            self.integration_id: str = content['integration_id']
            # boolean: whether or not the integration supports log collection
            self.log_collection: bool = 'log collection' in content['categories']
            # boolean: whether or not the integration has public facing docs
            self.is_public: bool = content['is_public']

        # The log source defined in the log pipeline for this integration. This is populated after parsing pipelines.
        self.log_source_name: Optional[str] = None

        # All the log sources defined in the README (in theory only one or zero). Useful to alert if multiple sources
        # are defined in the README.
        self.source_names_readme: List[str] = self.get_log_sources_in_readme()

    def set_log_source_name(self, log_source_name: str) -> None:
        self.log_source_name = log_source_name

    def get_log_sources_in_readme(self) -> List[str]:
        readme_file = os.path.join(INTEGRATIONS_CORE, self.dir_name, "README.md")
        with open(readme_file, 'r') as f:
            content = f.read()

        code_sections: List[str] = re.findall(r'(```.*?```|`.*?`)', content, re.DOTALL)
        sources = set(re.findall(r'(?:"source"|source): "?(\w+)"?', "\n".join(code_sections), re.MULTILINE))

        return list(sources)

    def is_self(self, other_check_name) -> bool:
        candidates = [self.dir_name.lower(), self.name.lower(), self.integration_id.lower()]
        for source in self.source_names_readme:
            candidates.append(source.lower())
        if other_check_name.lower() in candidates:
            return True

        return False

    def validate(self) -> List[str]:
        # TODO: Check json file from web-ui
        if not self.is_public:
            return []

        errors = set()
        if not self.log_source_name:
            # This check doesn't appear to have a log pipeline.
            if self.log_collection:
                errors.add(ERR_UNEXPECTED_LOG_COLLECTION_CAT)
            if self.source_names_readme:
                errors.add(ERR_UNEXPECTED_LOG_DOC)
        else:
            # This check has a log pipeline, let's validate it.
            if not self.log_collection:
                errors.add(ERR_MISSING_LOG_COLLECTION_CAT)
            if not self.source_names_readme:
                errors.add(ERR_MISSING_LOG_DOC)
            if len(self.source_names_readme) > 1:
                errors.add(ERR_MULTIPLE_SOURCES)

        # Filter out some expected edge cases:
        for exp_err in EXCEPTIONS.get(self.name, []):
            if exp_err in errors:
                errors.remove(exp_err)
        return list(errors)

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, ", ".join(f"{k}={v}" for k, v in self.__dict__.items()))


def print_err(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def get_all_checks() -> List[CheckDefinition]:
    check_dirs = [
        d for d in os.listdir(INTEGRATIONS_CORE)
        if not d.startswith('.')
        and os.path.isfile(os.path.join(INTEGRATIONS_CORE, d, "manifest.json"))
    ]
    check_dirs.sort()

    all_checks = []
    for check_dir in check_dirs:
        all_checks.append(CheckDefinition(check_dir))

    return all_checks


def get_all_log_pipelines_ids():
    files = [os.path.join(LOGS_BACKEND_INTGS_ROOT, f) for f in os.listdir(LOGS_BACKEND_INTGS_ROOT)]
    files = [f for f in files if os.path.isfile(f)]
    files.sort()
    for file in files:
        with open(file, 'r') as f:
            yield yaml.load(f, Loader=yaml.SafeLoader)['id']


def get_check_for_pipeline(log_source_name, agt_intgs_checks):
    for check in agt_intgs_checks:
        if check.is_self(log_source_name):
            return check
    return None


if len(sys.argv) != 2:
    print_err("This script requires a single JSON file as an argument.")
    sys.exit(1)

with open(sys.argv[1]) as f:
    logs_to_metrics_mapping = json.load(f)

all_checks = list(get_all_checks())
for pipeline_id in get_all_log_pipelines_ids():
    if check := get_check_for_pipeline(pipeline_id, all_checks):
        check.set_log_source_name(pipeline_id)


validation_errors_per_check = {}
for check in all_checks:
    errors = check.validate()
    if errors:
        validation_errors_per_check[check.name] = errors


# Filter to only agt integrations checks
for check, errs in validation_errors_per_check.items():
    for err in errs:
        print(f"{check}: {err}")
