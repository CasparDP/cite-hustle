"""Journal registry for field-specific journals"""
from dataclasses import dataclass
from typing import List, Dict


@dataclass
class Journal:
    """Represents an academic journal"""
    name: str
    issn: str
    field: str  # 'accounting', 'finance', 'economics'
    publisher: str = ""


class JournalRegistry:
    """Central registry for all field-specific journals"""
    
    # Top Accounting Journals
    ACCOUNTING = [
        Journal("The Accounting Review", "0001-4826", "accounting", "AAA"),
        Journal("Journal of Accounting and Economics", "0165-4101", "accounting", "Elsevier"),
        Journal("Journal of Accounting Research", "0021-8456", "accounting", "Wiley"),
        Journal("Contemporary Accounting Research", "0823-9150", "accounting", "Wiley"),
        Journal("Accounting, Organizations and Society", "0361-3682", "accounting", "Elsevier"),
        Journal("Review of Accounting Studies", "1380-6653", "accounting", "Springer"),
    ]
    
    # Top Finance Journals
    FINANCE = [
        Journal("Journal of Finance", "0022-1082", "finance", "Wiley"),
        Journal("Journal of Financial Economics", "0304-405X", "finance", "Elsevier"),
        Journal("Review of Financial Studies", "0893-9454", "finance", "Oxford"),
        Journal("Journal of Financial and Quantitative Analysis", "0022-1090", "finance", "Cambridge"),
        Journal("Financial Management", "0046-3892", "finance", "Wiley"),
    ]
    
    # Top Economics Journals
    ECONOMICS = [
        Journal("American Economic Review", "0002-8282", "economics", "AEA"),
        Journal("Econometrica", "0012-9682", "economics", "Wiley"),
        Journal("Quarterly Journal of Economics", "0033-5533", "economics", "Oxford"),
        Journal("Journal of Political Economy", "0022-3808", "economics", "Chicago"),
        Journal("Review of Economic Studies", "0034-6527", "economics", "Oxford"),
        Journal("Journal of Economic Literature", "0022-0515", "economics", "AEA"),
        Journal("Journal of Economic Perspectives", "0895-3309", "economics", "AEA"),
        Journal("Journal of Labor Economics", "0734-306X", "economics", "Chicago"),
    ]
    
    @classmethod
    def get_all_journals(cls) -> List[Journal]:
        """Get all journals across all fields"""
        return cls.ACCOUNTING + cls.FINANCE + cls.ECONOMICS
    
    @classmethod
    def get_by_field(cls, field: str) -> List[Journal]:
        """Get journals by research field"""
        field = field.lower()
        if field == 'accounting':
            return cls.ACCOUNTING
        elif field == 'finance':
            return cls.FINANCE
        elif field == 'economics':
            return cls.ECONOMICS
        elif field == 'all':
            return cls.get_all_journals()
        else:
            raise ValueError(f"Unknown field: {field}. Use 'accounting', 'finance', 'economics', or 'all'")
    
    @classmethod
    def get_journal_dict(cls) -> Dict[str, Journal]:
        """Get dictionary of journals by ISSN"""
        return {j.issn: j for j in cls.get_all_journals()}
    
    @classmethod
    def get_issn_list(cls, field: str = 'all') -> List[str]:
        """Get list of ISSNs for a field"""
        return [j.issn for j in cls.get_by_field(field)]
