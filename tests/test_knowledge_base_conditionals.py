"""Tests for knowledge base conditional logic."""

from __future__ import annotations

import pytest

from app.services.knowledge_base_conditionals import (
    get_conditional_companies,
    process_conditionals,
    validate_conditional_syntax,
)


class TestProcessConditionals:
    """Tests for the process_conditionals function."""

    def test_process_single_matching_conditional(self) -> None:
        """Test processing a single conditional block that matches."""
        content = '<kb-if company="ACME Corp">ACME-specific content</kb-if>'
        result = process_conditionals(content, company_name="ACME Corp")
        assert result == "ACME-specific content"

    def test_process_single_non_matching_conditional(self) -> None:
        """Test processing a single conditional block that doesn't match."""
        content = '<kb-if company="ACME Corp">ACME-specific content</kb-if>'
        result = process_conditionals(content, company_name="Other Corp")
        assert result == ""

    def test_process_no_company_context(self) -> None:
        """Test processing conditionals when no company is in context."""
        content = '<kb-if company="ACME Corp">ACME-specific content</kb-if>'
        result = process_conditionals(content, company_name=None)
        assert result == ""

    def test_process_multiple_conditionals(self) -> None:
        """Test processing multiple conditional blocks."""
        content = '''
        <kb-if company="ACME Corp">ACME content</kb-if>
        <p>Common content</p>
        <kb-if company="XYZ Inc">XYZ content</kb-if>
        '''
        
        # Test with ACME
        result = process_conditionals(content, company_name="ACME Corp")
        assert "ACME content" in result
        assert "Common content" in result
        assert "XYZ content" not in result
        
        # Test with XYZ
        result = process_conditionals(content, company_name="XYZ Inc")
        assert "ACME content" not in result
        assert "Common content" in result
        assert "XYZ content" in result

    def test_case_insensitive_matching(self) -> None:
        """Test that company name matching is case-insensitive."""
        content = '<kb-if company="Acme Corp">ACME content</kb-if>'
        result = process_conditionals(content, company_name="acme corp")
        assert result == "ACME content"

    def test_process_conditional_with_html_content(self) -> None:
        """Test processing conditionals containing HTML."""
        content = '''
        <kb-if company="ACME Corp">
            <h2>ACME Title</h2>
            <p>ACME paragraph</p>
            <img src="/acme-logo.png" alt="ACME Logo" />
        </kb-if>
        '''
        result = process_conditionals(content, company_name="ACME Corp")
        assert "<h2>ACME Title</h2>" in result
        assert "<p>ACME paragraph</p>" in result
        assert 'src="/acme-logo.png"' in result

    def test_process_empty_content(self) -> None:
        """Test processing empty content."""
        assert process_conditionals("", company_name="ACME") == ""
        assert process_conditionals(None, company_name="ACME") == None

    def test_process_content_without_conditionals(self) -> None:
        """Test processing content that has no conditionals."""
        content = "<p>Regular content without conditionals</p>"
        result = process_conditionals(content, company_name="ACME Corp")
        assert result == content

    def test_multiline_conditional_content(self) -> None:
        """Test conditionals with multi-line content."""
        content = '''<kb-if company="Test Company">
Line 1
Line 2
Line 3
</kb-if>'''
        result = process_conditionals(content, company_name="Test Company")
        assert "Line 1" in result
        assert "Line 2" in result
        assert "Line 3" in result

    def test_conditional_with_special_characters_in_company_name(self) -> None:
        """Test conditionals with company names containing special characters."""
        content = '<kb-if company="Smith & Jones Ltd.">Content</kb-if>'
        result = process_conditionals(content, company_name="Smith & Jones Ltd.")
        assert result == "Content"


class TestGetConditionalCompanies:
    """Tests for the get_conditional_companies function."""

    def test_extract_single_company(self) -> None:
        """Test extracting a single company name."""
        content = '<kb-if company="ACME Corp">Content</kb-if>'
        companies = get_conditional_companies(content)
        assert companies == ["ACME Corp"]

    def test_extract_multiple_companies(self) -> None:
        """Test extracting multiple company names."""
        content = '''
        <kb-if company="ACME Corp">ACME</kb-if>
        <kb-if company="XYZ Inc">XYZ</kb-if>
        <kb-if company="Test Ltd">Test</kb-if>
        '''
        companies = get_conditional_companies(content)
        assert len(companies) == 3
        assert "ACME Corp" in companies
        assert "XYZ Inc" in companies
        assert "Test Ltd" in companies

    def test_extract_duplicate_companies(self) -> None:
        """Test that duplicate company names are deduplicated."""
        content = '''
        <kb-if company="ACME Corp">Section 1</kb-if>
        <kb-if company="ACME Corp">Section 2</kb-if>
        '''
        companies = get_conditional_companies(content)
        assert companies == ["ACME Corp"]

    def test_extract_case_insensitive_duplicates(self) -> None:
        """Test that duplicates are detected case-insensitively."""
        content = '''
        <kb-if company="ACME Corp">Section 1</kb-if>
        <kb-if company="acme corp">Section 2</kb-if>
        '''
        companies = get_conditional_companies(content)
        assert len(companies) == 1

    def test_extract_from_empty_content(self) -> None:
        """Test extracting from empty content."""
        assert get_conditional_companies("") == []
        assert get_conditional_companies(None) == []

    def test_extract_from_content_without_conditionals(self) -> None:
        """Test extracting from content without conditionals."""
        content = "<p>Regular content</p>"
        companies = get_conditional_companies(content)
        assert companies == []


class TestValidateConditionalSyntax:
    """Tests for the validate_conditional_syntax function."""

    def test_validate_correct_syntax(self) -> None:
        """Test validation of correct conditional syntax."""
        content = '<kb-if company="ACME">Content</kb-if>'
        errors = validate_conditional_syntax(content)
        assert errors == []

    def test_validate_unclosed_tag(self) -> None:
        """Test validation detects unclosed tags."""
        content = '<kb-if company="ACME">Content'
        errors = validate_conditional_syntax(content)
        assert len(errors) > 0
        assert "Mismatched" in errors[0]

    def test_validate_unopened_tag(self) -> None:
        """Test validation detects unopened closing tags."""
        content = 'Content</kb-if>'
        errors = validate_conditional_syntax(content)
        assert len(errors) > 0
        assert "Mismatched" in errors[0]

    def test_validate_empty_company_attribute(self) -> None:
        """Test validation detects empty company attributes."""
        content = '<kb-if company="">Content</kb-if>'
        errors = validate_conditional_syntax(content)
        assert len(errors) > 0
        assert "company name" in errors[0].lower()

    def test_validate_nested_conditionals(self) -> None:
        """Test validation detects nested conditionals."""
        content = '<kb-if company="A"><kb-if company="B">Nested</kb-if></kb-if>'
        errors = validate_conditional_syntax(content)
        assert len(errors) > 0
        assert "Nested" in errors[0]

    def test_validate_multiple_errors(self) -> None:
        """Test validation can detect multiple errors."""
        content = '<kb-if company="">Unclosed and empty'
        errors = validate_conditional_syntax(content)
        assert len(errors) >= 1

    def test_validate_empty_content(self) -> None:
        """Test validation of empty content."""
        assert validate_conditional_syntax("") == []
        assert validate_conditional_syntax(None) == []

    def test_validate_content_without_conditionals(self) -> None:
        """Test validation of content without conditionals."""
        content = "<p>Regular content</p>"
        errors = validate_conditional_syntax(content)
        assert errors == []
