# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.bill.business.service import BillService
from modules.vendor.business.service import VendorService
from modules.bill_line_item.business.service import BillLineItemService
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.project.business.service import ProjectService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/bill", tags=["web", "bill"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_bills(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all bills.
    """
    bills = BillService().read_all()
    vendors = VendorService().read_all()
    
    # Create a mapping of vendor_id to vendor_name
    vendor_map = {vendor.id: vendor.name for vendor in vendors}
    
    # Add vendor names to bills
    bills_with_vendors = []
    for bill in bills:
        bill_dict = bill.to_dict()
        if bill.vendor_id and bill.vendor_id in vendor_map:
            bill_dict['vendor_name'] = vendor_map[bill.vendor_id]
        bills_with_vendors.append(bill_dict)
    
    return templates.TemplateResponse(
        "bill/list.html",
        {
            "request": request,
            "bills": bills_with_vendors,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_bill(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create bill form.
    """
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "bill/create.html",
        {
            "request": request,
            "vendors": vendors,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_bill(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a bill.
    """
    bill = BillService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    
    # Find the vendor name if bill has a vendor_id
    vendor_name = None
    if bill and bill.vendor_id:
        for vendor in vendors:
            if vendor.id == bill.vendor_id:
                vendor_name = vendor.name
                break
    
    # Fetch line items associated with this bill
    line_items = []
    if bill and bill.id:
        line_items = BillLineItemService().read_by_bill_id(bill_id=bill.id)
    
    bill_dict = bill.to_dict()
    if vendor_name:
        bill_dict['vendor_name'] = vendor_name
    
    return templates.TemplateResponse(
        "bill/view.html",
        {
            "request": request,
            "bill": bill_dict,
            "line_items": [line_item.to_dict() for line_item in line_items],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_bill(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a bill.
    """
    bill = BillService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor public_id if bill has a vendor_id
    vendor_public_id = None
    if bill and bill.vendor_id:
        for vendor in vendors:
            if vendor.id == bill.vendor_id:
                vendor_public_id = vendor.public_id
                break
    
    # Fetch line items associated with this bill
    line_items = []
    if bill and bill.id:
        line_items = BillLineItemService().read_by_bill_id(bill_id=bill.id)
    
    bill_dict = bill.to_dict()
    if vendor_public_id:
        bill_dict['vendor_public_id'] = vendor_public_id
    
    return templates.TemplateResponse(
        "bill/edit.html",
        {
            "request": request,
            "bill": bill_dict,
            "vendors": vendors,
            "line_items": [line_item.to_dict() for line_item in line_items],
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
