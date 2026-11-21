"""Synthetic fixtures implementing the selected read-only provider surface."""
from typing import Protocol


class DemoProvider(Protocol):
    def snapshots(self): ...


class MockProviders:
    def snapshots(self):
        return [
            {'provider': 'Google Drive', 'item': 'Synthetic Demo evidence packet', 'mode': 'mock'},
            {'provider': 'Google Sheets', 'item': 'Synthetic Demo pipeline snapshot', 'mode': 'mock'},
            {'provider': 'Microsoft Outlook', 'item': 'Synthetic Demo interview notification', 'mode': 'mock'},
            {'provider': 'Gmail', 'item': 'Synthetic Demo confirmation message', 'mode': 'mock'},
        ]


providers: DemoProvider = MockProviders()
