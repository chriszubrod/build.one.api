import io
import types

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate

from entities.contract_labor.business.bill_service import _pdf_text, ContractLaborBillService


def test_pdf_text_escapes():
    assert _pdf_text('Smith & Sons <LLC>') == 'Smith &amp; Sons &lt;LLC&gt;'
    assert _pdf_text(None) == ''
    assert _pdf_text(123) == '123'


def test_generate_pdf_elements_survives_ampersand():
    line_items = [
        {
            'line_item': types.SimpleNamespace(
                description='Framing & trim <b>',
                line_date='2026-06-15',
                is_billable=True,
                price=100.0,
            ),
            'scc': types.SimpleNamespace(number='01-100'),
            'entry': types.SimpleNamespace(work_date='2026-06-15'),
        }
    ]
    project = types.SimpleNamespace(name='Tyne & Blvd <A>', abbreviation='TB3')
    elements = ContractLaborBillService()._generate_pdf_elements(
        vendor_name='Smith & Sons <X>',
        project=project,
        invoice_number='2026.06.15.TB3',
        bill_date='2026-06-15',
        due_date='2026-06-30',
        total_amount=100.0,
        line_items=line_items,
    )
    SimpleDocTemplate(io.BytesIO(), pagesize=letter).build(elements)
