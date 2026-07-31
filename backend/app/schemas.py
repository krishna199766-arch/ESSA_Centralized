"""Pydantic schemas: the canonical invoice representation (provider-independent)
and the API request/response shapes."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ----------------------------- canonical invoice -----------------------------
class Party(BaseModel):
    name: Optional[str] = None
    legal_name: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    bank: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class InvoiceMeta(BaseModel):
    number: Optional[str] = None
    date: Optional[str] = None
    due_date: Optional[str] = None
    challan_no: Optional[str] = None
    order_no: Optional[str] = None
    irn: Optional[str] = None
    ack_no: Optional[str] = None
    irn_date: Optional[str] = None
    eway_bill: Optional[str] = None
    tran_id: Optional[str] = None
    delivery_note: Optional[str] = None
    # consignment fields — read off the invoice when printed, otherwise
    # back-filled from the LR register (see services/lr_link.INVOICE_FROM_LR)
    lr_no: Optional[str] = None
    lr_date: Optional[str] = None
    transporter: Optional[str] = None
    destination: Optional[str] = None
    book_city: Optional[str] = None

    model_config = {"extra": "allow"}


class LineItemModel(BaseModel):
    sr: Optional[int] = None
    barcode: Optional[str] = None
    description: Optional[str] = None
    brand: Optional[str] = None
    design: Optional[str] = None
    size: Optional[str] = None
    hsn: Optional[str] = None
    qty: Optional[float] = None
    uom: Optional[str] = "PCS"
    mrp: Optional[float] = None
    rate: Optional[float] = None
    discount_pct: Optional[float] = 0.0
    discount_amount: Optional[float] = 0.0
    taxable_value: Optional[float] = None
    amount: Optional[float] = None

    model_config = {"extra": "allow"}


class Taxes(BaseModel):
    cgst_rate: float = 0.0
    cgst_amount: float = 0.0
    sgst_rate: float = 0.0
    sgst_amount: float = 0.0
    igst_rate: float = 0.0
    igst_amount: float = 0.0
    tds_amount: float = 0.0
    other_charges: float = 0.0
    freight: float = 0.0
    round_off: float = 0.0

    model_config = {"extra": "allow"}


class Totals(BaseModel):
    total_qty: Optional[float] = None
    sub_total: Optional[float] = None
    taxable_total: Optional[float] = None
    tax_total: Optional[float] = None
    grand_total: Optional[float] = None
    amount_in_words: Optional[str] = None

    model_config = {"extra": "allow"}


class CanonicalInvoice(BaseModel):
    document_type: str = "purchase_invoice"
    template_key: Optional[str] = None
    supplier: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    invoice: InvoiceMeta = Field(default_factory=InvoiceMeta)
    line_items: List[LineItemModel] = Field(default_factory=list)
    taxes: Taxes = Field(default_factory=Taxes)
    totals: Totals = Field(default_factory=Totals)
    meta: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


# ----------------------------- API i/o -----------------------------
class ExtractionOut(BaseModel):
    id: int
    document_id: int
    provider: str
    confidence: float
    warnings: List[str]
    field_flags: Dict[str, str]
    data: Dict[str, Any]
    is_correction: bool


class DocumentOut(BaseModel):
    id: int
    filename: str
    status: str
    supplier_id: Optional[int]
    supplier_name: Optional[str] = None
    grand_total: Optional[float] = None
    invoice_number: Optional[str] = None
    confidence: Optional[float] = None
    uploaded_at: Optional[str] = None


class ConfirmRequest(BaseModel):
    data: Dict[str, Any]                 # the human-corrected canonical invoice
    train: bool = True                   # update/create the supplier profile?


class SupplierOut(BaseModel):
    id: int
    name: str
    gstin: Optional[str]
    state: Optional[str]
    has_profile: bool
    profile_samples: int = 0
    document_count: int = 0
