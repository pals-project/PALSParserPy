"""Propose the latest stable native release; never auto-merge or publish."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def output(*args):
    return subprocess.check_output(args, text=True).strip()

def resolve(url, tag):
    refs = output("git", "ls-remote", url, "refs/tags/" + tag, "refs/tags/" + tag + "^{}")
    entries = [line.split() for line in refs.splitlines()]
    peeled = [sha for sha, ref in entries if ref.endswith("^{}")]
    return peeled[0] if peeled else entries[0][0]

def main():
    path = ROOT / "native-lock.json"
    lock = json.loads(path.read_text())
    release = json.loads(output("gh", "api", "repos/pals-project/PALSParserCpp/releases/latest"))
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"Expected stable release tag, got {tag!r}")
    if tuple(map(int, tag[1:].split('.'))) <= tuple(map(int, lock['version'].split('.'))):
        return
    url = "https://github.com/pals-project/PALSParserCpp.git"
    commit = resolve(url, tag)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "--bare", url, tmp], check=True)
        cmake = output("git", "--git-dir", tmp, "show", commit + ":CMakeLists.txt")
        header = output("git", "--git-dir", tmp, "show", commit + ":src/PALSParserCpp.h")
        old_header = output("git", "--git-dir", tmp, "show", lock['commit'] + ":src/PALSParserCpp.h")
    for name, source in lock['dependencies'].items():
        match = re.search(r"FetchContent_Declare\(\s*" + name + r"\s+GIT_REPOSITORY\s+(\S+)\s+GIT_TAG\s+(\S+)", cmake)
        if not match or match[1] != source['url']:
            raise ValueError(f"Review changed dependency declaration for {name}")
        source['tag'] = match[2]
        source['commit'] = resolve(source['url'], source['tag'])
    if header == old_header:
        loader = ROOT / 'palsparserpy/_clib.py'
        text = loader.read_text()
        hi = ', '.join(tag[1:].split('.'))
        text, count = re.subn(r'(SUPPORTED_LIBRARY_VERSIONS = \(\(\d+, \d+, \d+\), )\(\d+, \d+, \d+\)\)', r'\g<1>(' + hi + '))', text)
        assert count == 1
        loader.write_text(text)
    else:
        print('Public C header changed: maintainers must review bindings and supported versions before merging.')
    lock.update(version=tag[1:], commit=commit)
    path.write_text(json.dumps(lock, indent=2) + '\n')

if __name__ == '__main__':
    main()
