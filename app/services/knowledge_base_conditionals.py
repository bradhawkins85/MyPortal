"""Conditional logic processor for knowledge base articles.

This module provides functionality to evaluate and process conditional blocks
in knowledge base article content based on the viewing context (e.g., company).
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping

# Pattern to match conditional blocks
# Matches: <kb-if company="Company Name">content</kb-if>
_CONDITIONAL_PATTERN = re.compile(
    r'<kb-if\s+company="([^"]+)"\s*>(.*?)</kb-if>',
    re.DOTALL | re.IGNORECASE
)


def process_conditionals(
    content: str,
    *,
    company_name: str | None = None,
) -> str:
    """Process conditional blocks in article content based on context.
    
    This function evaluates <kb-if> blocks in the content and only includes
    content that matches the current context. Content that doesn't match
    is completely removed from the output.
    
    Args:
        content: The article content with conditional blocks
        company_name: The name of the company viewing the article (if any)
    
    Returns:
        Processed content with only matching conditional blocks included
    
    Example:
        >>> content = '<kb-if company="ACME">ACME content</kb-if>'
        >>> process_conditionals(content, company_name="ACME")
        'ACME content'
        >>> process_conditionals(content, company_name="Other")
        ''
    """
    if not content:
        return content
    
    def replace_conditional(match: re.Match[str]) -> str:
        """Replace a conditional block with its content if it matches."""
        condition_company = match.group(1).strip()
        block_content = match.group(2)
        
        # Check if the condition matches
        if company_name and condition_company:
            # Case-insensitive comparison
            if condition_company.lower() == company_name.lower():
                return block_content
        
        # No match - remove the entire block
        return ""
    
    # Process all conditional blocks
    processed = _CONDITIONAL_PATTERN.sub(replace_conditional, content)
    
    return processed


def get_conditional_companies(content: str) -> list[str]:
    """Extract all company names referenced in conditional blocks.
    
    This is useful for administrative purposes to see which companies
    have custom content in an article.
    
    Args:
        content: The article content with conditional blocks
    
    Returns:
        List of unique company names found in conditional blocks
    """
    if not content:
        return []
    
    companies: list[str] = []
    seen: set[str] = set()
    
    for match in _CONDITIONAL_PATTERN.finditer(content):
        company = match.group(1).strip()
        if company and company.lower() not in seen:
            companies.append(company)
            seen.add(company.lower())
    
    return companies


def validate_conditional_syntax(content: str) -> list[str]:
    """Validate conditional block syntax and return any errors.
    
    Args:
        content: The article content to validate
    
    Returns:
        List of error messages (empty if valid)
    """
    if not content:
        return []
    
    errors: list[str] = []
    
    # Check for unclosed kb-if tags
    open_tags = re.findall(r'<kb-if\s+company="[^"]*"\s*>', content, re.IGNORECASE)
    close_tags = re.findall(r'</kb-if>', content, re.IGNORECASE)
    
    if len(open_tags) != len(close_tags):
        errors.append(
            f"Mismatched conditional tags: {len(open_tags)} opening tags, "
            f"{len(close_tags)} closing tags"
        )
    
    # Check for empty company attributes
    empty_company = re.findall(r'<kb-if\s+company=""\s*>', content, re.IGNORECASE)
    if empty_company:
        errors.append("Conditional blocks must specify a company name")
    
    # Check for nested conditionals (not currently supported)
    matches = list(_CONDITIONAL_PATTERN.finditer(content))
    for i, match in enumerate(matches):
        block_content = match.group(2)
        if '<kb-if' in block_content.lower():
            errors.append(
                f"Nested conditional blocks are not supported (found in block for "
                f'company "{match.group(1)}")'
            )
    
    return errors
