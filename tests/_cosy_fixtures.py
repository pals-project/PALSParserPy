"""Shared PALS input builder for native and COSY execution tests."""

import textwrap


def document(elements, species="proton", pc="1e9", definitions=""):
    body = textwrap.indent(textwrap.dedent(elements).strip(), "          ")
    definitions = textwrap.indent(textwrap.dedent(definitions).strip(), "    ")
    return f'''PALS:
  facility:
{definitions}
    - main:
        kind: BeamLine
        line:
          - begin:
              kind: BeginningEle
              ReferenceP: {{species_ref: "{species}", pc_ref: {pc}}}
{body}
    - ring: {{kind: Lattice, branches: [main]}}
    - use: ring
'''
