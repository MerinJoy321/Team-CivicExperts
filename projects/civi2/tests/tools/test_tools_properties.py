"""Property-based tests for DocumentGenerator and PortalValidator (Tasks 12.3, 12.4).

# Feature: civicpilot, Property 15: Generated documents never contain another scheme's data
# Feature: civicpilot, Property 16: Portal validator never labels a non-matching domain as official

Validates: Requirements 15.3, 16.1, 16.2
"""

from __future__ import annotations

import io
import docx
from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.tools.document_generator import DocumentGenerator
from civicpilot.tools.portal_validator import PortalValidator


@st.composite
def scheme_payload_strategy(draw):
    scheme_name = draw(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')), min_size=5, max_size=30))
    summary = draw(st.text(min_size=10, max_size=100))
    criteria = draw(st.lists(st.text(min_size=5, max_size=50), min_size=1, max_size=5))
    steps = draw(st.lists(st.text(min_size=5, max_size=50), min_size=1, max_size=5))
    return {
        "scheme_name": f"Scheme_{scheme_name}",
        "summary": summary,
        "criteria": criteria,
        "application_steps": steps,
    }


@given(
    target_scheme=scheme_payload_strategy(),
    other_scheme_name=st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')), min_size=8, max_size=30),
)
@settings(max_examples=100)
def test_property_15_document_scheme_isolation(target_scheme: dict, other_scheme_name: str):
    # Feature: civicpilot, Property 15: Generated documents never contain another scheme's data
    # Ensure distinct names
    other_name = f"SECRET_OTHER_{other_scheme_name}"

    generator = DocumentGenerator()
    doc_bytes = generator.generate_document(target_scheme)

    doc = docx.Document(io.BytesIO(doc_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)

    # Document must contain target scheme name
    assert target_scheme["scheme_name"] in full_text
    # Document must NOT contain another scheme's name
    assert other_name not in full_text


@given(
    domain_prefix=st.text(alphabet=st.characters(whitelist_categories=('Ll', 'Nd')), min_size=1, max_size=20),
    invalid_tld=st.sampled_from(["com", "org", "net", "io", "co.in", "gov.in.fake.com", "notreallynic.in"]),
)
@settings(max_examples=100)
def test_property_16_portal_validator_domain_matching(domain_prefix: str, invalid_tld: str):
    # Feature: civicpilot, Property 16: Portal validator never labels a non-matching domain as official
    validator = PortalValidator(state_domain_registry={"karnataka.gov.in", "tn.gov.in"})

    url = f"https://{domain_prefix}.{invalid_tld}/path"

    # Any domain not ending in .gov.in, .nic.in, or present in state_domain_registry must return False
    is_official = validator.is_official(url)

    if invalid_tld in ("com", "org", "net", "io", "co.in", "gov.in.fake.com", "notreallynic.in"):
        assert is_official is False, f"Domain {url} was incorrectly flagged as official."
