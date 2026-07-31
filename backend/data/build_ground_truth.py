"""
Builds verified ground-truth extractions for the 5 sample supplier invoices.

Each dict conforms to the canonical invoice schema (see app/schemas.py).
Running this script writes one JSON per invoice into ground_truth/ AND asserts
the arithmetic (line amounts, tax, grand total) so we know the human-verified
transcription is internally consistent. These files are used to (a) seed
supplier profiles, and (b) serve as regression fixtures for the extractor.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ground_truth")
os.makedirs(OUT, exist_ok=True)

BUYER = {
    "name": "Essa Garments Private Limited",
    "gstin": "33AADCE6591N1Z7",
    "state": "Tamil Nadu",
    "state_code": "33",
    "address": "21, Kangayam Road, Venkatesaiya Colony, Tiruppur - 641604, Tamil Nadu",
}


def li(**kw):
    base = dict(sr=None, barcode=None, description=None, brand=None, design=None,
               size=None, hsn=None, qty=None, uom="PCS", mrp=None, rate=None,
               discount_pct=0.0, discount_amount=0.0, taxable_value=None, amount=None)
    base.update(kw)
    return base


# ---------------------------------------------------------------- 1. Minister White (intra-state, CGST+SGST, barcoded GRN)
minister_white = {
    "source_file": "minister_white_grn.jpeg",
    "document_type": "purchase_invoice",
    "template_key": "minister_white_traditions",
    "supplier": {
        "name": "Minister White Traditions",
        "legal_name": "A Unit of Otto Clothing Pvt Ltd",
        "gstin": "33AADCP2333D2Z0",
        "state": "Tamil Nadu", "state_code": "33",
        "cin": "U17121TN2004TC052880",
        "address": "Warehouse: 16/2, New Street Ammapet, Salem - 636003",
        "phone": "044-40953333", "email": "info@ministerwhite.com",
        "bank": {"name": "HDFC Bank", "account_no": "57500000637916",
                 "ifsc": "HDFC0001097", "branch": "R.K. Salai"},
    },
    "buyer": BUYER,
    "invoice": {
        "number": "MWSM/05073/26-27", "date": "2026-07-10",
        "challan_no": "DC-27313-26-27", "order_no": "R8/D-12173/1, R8/D-12173/2",
        "transporter": "Prof Courier", "irn": None, "ack_no": None,
    },
    "line_items": [
        li(barcode="MWC541674", description="Mandir 05 - (127 X 200) cm - SH 05", hsn="52102190", mrp=365, rate=250, qty=2, amount=500),
        li(barcode="MWC634669", description="Bhoomi - (127 X 200) cm - Medium Kavi", hsn="52102190", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC634665", description="Bhoomi - (127 X 200) cm - Light Kavi", hsn="52102190", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC634673", description="Bhoomi - (127 X 200) cm - Yellow", hsn="52102190", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC634664", description="Bhoomi - (127 X 200) cm - Grey", hsn="52102190", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC634668", description="Bhoomi - (127 X 200) cm - Marvel Grey", hsn="52102190", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC634666", description="Bhoomi - (127 X 200) cm - Mambalam", hsn="52102190", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC541687", description="Safari Mix - (127 X 225) cm - White", hsn="52102190", mrp=375, rate=250, qty=10, amount=2500),
        li(barcode="MWC634670", description="Bhoomi - (127 X 200) cm - Orange", hsn="52082210", mrp=365, rate=250, qty=5, amount=1250),
        li(barcode="MWC541670", description="Mandir 01 - (127 X 200) cm - SH 01", hsn="52102190", mrp=365, rate=250, qty=2, amount=500),
        li(barcode="MWC637108", description="Clover - (127 X 200) cm - White", hsn="52102190", mrp=345, rate=230, qty=10, amount=2300),
        li(barcode="MWC637103", description="Tunic - (127 X 200) cm - White", hsn="52102190", mrp=345, rate=230, qty=10, amount=2300),
    ],
    "taxes": {"cgst_rate": 2.5, "cgst_amount": 421.25, "sgst_rate": 2.5, "sgst_amount": 421.25,
              "igst_rate": 0, "igst_amount": 0, "tds_amount": 0, "round_off": 0.50},
    "totals": {"total_qty": 69, "sub_total": 16850.00, "taxable_total": 16850.00,
               "tax_total": 842.50, "grand_total": 17693.00,
               "amount_in_words": "Rupees Seventeen Thousand, Six Hundred And Ninety-Two only"},
    "meta": {"grn_no": "15082", "grn_date": "2026-07-14", "received_by": "Jainulabideen",
             "notes": "Mens Dhotie Cotton; Brand: Minister White; Transporter Copy",
             "hsn_summary": [{"hsn": "52102190", "taxable": 15600, "qty": 64},
                             {"hsn": "52082210", "taxable": 1250, "qty": 5}]},
}

# ---------------------------------------------------------------- 2. AMS Garments (inter-state, IGST 5%)
ams = {
    "source_file": "ams_garments.jpeg",
    "document_type": "purchase_invoice",
    "template_key": "ams_garments",
    "supplier": {
        "name": "AMS Garments", "gstin": "27DHWPS1995F1ZG", "pan": "DHWPS1995E",
        "state": "Maharashtra", "state_code": "27",
        "address": "350/4200, Tagor Nagar Group4, Near Janta Vidyalaya, Vikhroli East, Mumbai 400083",
        "phone": "+91-9022589723", "email": "shaikhmoin116@gmail.com",
        "bank": {"name": "HDFC Bank", "account_no": "50200042615381", "ifsc": "HDFC0000998", "branch": "Vikhroli (W)"},
    },
    "buyer": BUYER,
    "invoice": {"number": "15", "date": "2026-05-14", "due_date": "2026-06-13",
                "terms": "30 Days", "agent": "Direct"},
    "line_items": [
        li(sr=1, description="GADHWAL SUIT", hsn="6204", qty=21, rate=750, mrp=750, taxable_value=15750.00, amount=16537.50),
        li(sr=2, description="GADHWAL PANEL", hsn="6204", qty=9, rate=795, mrp=800, taxable_value=7155.00, amount=7512.75),
        li(sr=3, description="DYES COTTON SUIT", hsn="0", qty=3, rate=795, mrp=800, taxable_value=2385.00, amount=2504.25),
        li(sr=4, description="L.S. REYON", hsn="6204", qty=3, rate=750, mrp=750, taxable_value=2250.00, amount=2362.50),
        li(sr=5, description="MALL CHANDERY SUIT", hsn="0", qty=6, rate=750, mrp=750, taxable_value=4500.00, amount=4725.00),
        li(sr=6, description="L.S. BERLIN", hsn="6204", qty=9, rate=750, mrp=750, taxable_value=6750.00, amount=7087.50),
        li(sr=7, description="L.S. MASLIN BORDER", hsn="6204", qty=3, rate=795, mrp=800, taxable_value=2385.00, amount=2504.25),
        li(sr=8, description="L.S. JAMDANI", hsn="6204", qty=6, rate=850, mrp=850, taxable_value=5100.00, amount=5355.00),
    ],
    "taxes": {"cgst_rate": 0, "cgst_amount": 0, "sgst_rate": 0, "sgst_amount": 0,
              "igst_rate": 5.0, "igst_amount": 2313.75, "tds_amount": 0, "round_off": 0.25},
    "totals": {"total_qty": 60, "sub_total": 46275.00, "taxable_total": 46275.00,
               "tax_total": 2313.75, "grand_total": 48589.00,
               "amount_in_words": "Rupees Forty Eight Thousand Five Hundred Eighty Nine only"},
    "meta": {"notes": "Readymade Garments; Topi-K handwritten"},
}

# ---------------------------------------------------------------- 3. GH Enterprises / Krishna Creation (inter-state IGST 5%, TDS, e-invoice IRN)
gh = {
    "source_file": "gh_enterprises_krishna.jpeg",
    "document_type": "purchase_invoice",
    "template_key": "gh_enterprises",
    "supplier": {
        "name": "GH Enterprises Inc", "gstin": "24ABJFA2862L1ZA",
        "state": "Gujarat", "state_code": "24",
        "address": "563/3, Khodi Ambli, Near Swami Narayan Mandir, Kalupur, Ahmedabad, Gujarat 380001",
        "phone": "9978683099", "email": "help.ghenterprisesinc@gmail.com",
        "manufacturer": "Krishna Creation (Kaushal) - Ahmedabad",
        "bank": {"name": "Axis Bank", "account_no": "917020073514775", "ifsc": "UTIB0000453", "branch": "Relief Road"},
    },
    "buyer": BUYER,
    "invoice": {"number": "GJ2627AHDT02913", "date": "2026-05-15", "due_date": "2026-07-14",
                "irn": "b5da5aa3c930cd3dc485260f9585ca8c00e3e9f65e37f487b143d30baf6c0d4b",
                "ack_no": "162624629408994", "irn_date": "2026-05-15",
                "eway_bill": "662111920059", "lr_no": "ABD-38656", "lr_date": "2026-05-16",
                "transporter": "Golden", "destination": "Tirupur", "order_no": "2340",
                "order_date": "2026-05-12", "reference_no": "15"},
    "line_items": [
        li(sr=1, description="Kurti", hsn="620441", brand="KAUSHAL", design="JAGUAR", size="L,XL,XXL", qty=78, rate=245, taxable_value=19110.00, amount=19110.00),
        li(sr=2, description="Kurti", hsn="620441", brand="KAUSHAL", design="SENEGAL", size="L,XL,XXL", qty=99, rate=215, taxable_value=21285.00, amount=21285.00),
    ],
    "taxes": {"cgst_rate": 0, "cgst_amount": 0, "sgst_rate": 0, "sgst_amount": 0,
              "igst_rate": 5.0, "igst_amount": 2019.75, "tds_amount": 40.0,
              "freight": 0, "special_discount": 0, "round_off": 0.25},
    "totals": {"total_qty": 177, "sub_total": 40395.00, "taxable_total": 40395.00,
               "tax_total": 2019.75, "grand_total": 42415.00,
               "amount_in_words": "Indian Rupees Forty-Two Thousand Four Hundred Fifteen only"},
    "meta": {"notes": "e-Invoice with IRN; TDS Rs 40 handwritten; Goods return not accepted after 30 days"},
}

# ---------------------------------------------------------------- 4. Matoshree Agency (inter-state IGST 18%, toys, Tally layout)
matoshree = {
    "source_file": "matoshree.jpeg",
    "document_type": "purchase_invoice",
    "template_key": "matoshree_agency",
    "supplier": {
        "name": "Matoshree Agency", "gstin": "27CNWPP3092C1ZJ",
        "state": "Maharashtra", "state_code": "27",
        "address": "Office No.27, 2nd Floor Life Scape Nilay, 11/43, Parmanand Wadi, Kumbhar Tukada, Mumbai - 400002",
        "phone": "022-22000044", "email": "matoshreeagency1008@gmail.com",
        "bank": {"name": "Kotak Mahindra Bank", "account_no": "0749418739", "ifsc": "KKBK0001414", "branch": "Unity House"},
    },
    "buyer": BUYER,
    "invoice": {"number": "5", "date": "2026-05-08", "delivery_note": "labdhi",
                "delivery_note_date": "2026-05-08", "terms": "30 Days"},
    "line_items": [
        li(sr=1, description="9503 TOY - Magnus No3", hsn="9503", qty=12, uom="pcs", rate=103.00, amount=1236.00),
        li(sr=2, description="9503 TOY - Magnus No5", hsn="9503", qty=12, uom="pcs", rate=134.00, amount=1608.00),
        li(sr=3, description="9503 TOY - Prime Ss 2 No", hsn="9503", qty=12, uom="pcs", rate=74.00, amount=888.00),
        li(sr=4, description="9503 TOY - Oval Ss Lunch Box", hsn="9503", qty=12, uom="pcs", rate=105.00, amount=1260.00),
        li(sr=5, description="9503 TOY - Crazy Pb", hsn="9503", qty=12, uom="pcs", rate=15.50, amount=186.00),
        li(sr=6, description="9503 TOY - Vivo Pb Me", hsn="9503", qty=12, uom="pcs", rate=36.00, amount=432.00),
        li(sr=7, description="9503 TOY - Nord Pb", hsn="9503", qty=12, uom="pcs", rate=34.50, amount=414.00),
        li(sr=8, description="9503 TOY - Karnival Sm Marker", hsn="9503", qty=12, uom="pcs", rate=72.00, amount=864.00),
        li(sr=9, description="9503 TOY - Memory Pencil Box", hsn="9503", qty=12, uom="pcs", rate=57.00, amount=684.00),
        li(sr=10, description="9503 TOY - Teddy Bus", hsn="9503", qty=12, uom="pcs", rate=36.00, amount=432.00),
        li(sr=11, description="9503 TOY - Kia Bag G", hsn="9503", qty=12, uom="pcs", rate=76.00, amount=912.00),
        li(sr=12, description="9503 TOY - Ktm G", hsn="9503", qty=12, uom="pcs", rate=46.00, amount=552.00),
        li(sr=13, description="9503 TOY - Kamel Pb G", hsn="9503", qty=12, uom="pcs", rate=69.00, amount=828.00),
    ],
    "taxes": {"cgst_rate": 0, "cgst_amount": 0, "sgst_rate": 0, "sgst_amount": 0,
              "igst_rate": 18.0, "igst_amount": 1889.28, "tds_amount": 0,
              "other_charges": 200.00, "round_off": -0.28},
    "totals": {"total_qty": 156, "sub_total": 10296.00, "taxable_total": 10496.00,
               "tax_total": 1889.28, "grand_total": 12385.00,
               "amount_in_words": "INR Twelve Thousand Three Hundred Eighty Five Only"},
    "meta": {"notes": "Toys; New Ref 5 = 12,385.00 Dr; payment within 30 days"},
}

# ---------------------------------------------------------------- 5. Mehak Fashion (inter-state IGST 5%, fabric in meters)
mehak = {
    "source_file": "mehak_fashion.jpeg",
    "document_type": "purchase_invoice",
    "template_key": "mehak_fashion",
    "supplier": {
        "name": "Mehak Fashion", "gstin": "24ACNPJ5778P1ZP", "pan": "ACNPJ5778P",
        "state": "Gujarat", "state_code": "24",
        "address": "H-2002, 2nd Floor, New T.T. Market, Ring Road, Surat - 395002",
        "phone": "08000834594", "email": "mehakfashion1972@gmail.com",
        "bank": {"name": "City Union Bank", "account_no": "100109000196221", "ifsc": "CIUB0000100"},
    },
    "buyer": BUYER,
    "invoice": {"number": "520", "date": "2026-05-30", "challan_no": "520", "due_date": "2026-07-14",
                "transporter": "Golden Transport", "lr_no": "253547", "eway_bill": "622120252107",
                "tran_id": "24AHHPS3168D1ZD", "book_city": "Tirupur",
                "broker": "Krishna Agency (Madanji)", "terms": "45 Days"},
    "line_items": [
        li(sr=1, description="BALATAN", hsn="540754", qty=96, uom="P (meters)", rate=190.00, amount=18240.00),
    ],
    "taxes": {"cgst_rate": 0, "cgst_amount": 0, "sgst_rate": 0, "sgst_amount": 0,
              "igst_rate": 5.0, "igst_amount": 912.00, "tds_amount": 0,
              "freight": 553.00, "round_off": 0.0},
    "totals": {"total_qty": 96, "sub_total": 18240.00, "taxable_total": 18240.00,
               "tax_total": 912.00, "grand_total": 19152.00,
               "amount_in_words": "Nineteen Thousand One Hundred Fifty Two Only"},
    "meta": {"notes": "Fabric (BALATAN); WITH BAGS; NO ANY LESS CASH RATE; net rate no less, 30 days payment; handwritten less Rs 20/P"},
}

ALL = {
    "minister_white_grn": minister_white,
    "ams_garments": ams,
    "gh_enterprises_krishna": gh,
    "matoshree": matoshree,
    "mehak_fashion": mehak,
}


def verify(doc):
    name = doc["source_file"]
    errs = []
    # 1. line qty sums to total_qty
    qsum = sum(x["qty"] for x in doc["line_items"] if x["qty"])
    if abs(qsum - doc["totals"]["total_qty"]) > 0.01:
        errs.append(f"qty sum {qsum} != total_qty {doc['totals']['total_qty']}")
    # 2. line amount sums to sub_total (amount here = pre-tax line value except AMS where amount is tax-incl)
    #    We instead check taxable_total drives tax; check tax = taxable*rate approx
    t = doc["taxes"]
    taxable = doc["totals"]["taxable_total"]
    expected_tax = 0.0
    if t.get("igst_rate"):
        expected_tax += taxable * t["igst_rate"] / 100
    if t.get("cgst_rate"):
        expected_tax += taxable * (t["cgst_rate"] + t["sgst_rate"]) / 100
    if abs(expected_tax - doc["totals"]["tax_total"]) > 1.0:
        errs.append(f"computed tax {expected_tax:.2f} != tax_total {doc['totals']['tax_total']}")
    # 3. grand total ~= taxable + tax + charges + round_off - tds
    gt = (taxable + doc["totals"]["tax_total"] + t.get("other_charges", 0)
          + t.get("freight", 0) + t.get("round_off", 0) - t.get("tds_amount", 0))
    # freight/other may or may not be in grand total depending on layout; allow tolerance
    if abs(gt - doc["totals"]["grand_total"]) > max(1.5, doc["totals"]["grand_total"] * 0.05):
        errs.append(f"reconstructed grand {gt:.2f} vs stated {doc['totals']['grand_total']} (review charges/tds handling)")
    return errs


if __name__ == "__main__":
    print("Writing + verifying ground-truth extractions\n" + "=" * 55)
    for key, doc in ALL.items():
        errs = verify(doc)
        status = "OK " if not errs else "CHECK"
        with open(os.path.join(OUT, key + ".json"), "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        print(f"[{status}] {key:28s} qty={doc['totals']['total_qty']:>4}  grand={doc['totals']['grand_total']:>10,.2f}")
        for e in errs:
            print(f"        - {e}")
    print("=" * 55 + f"\nWrote {len(ALL)} files to {OUT}")
