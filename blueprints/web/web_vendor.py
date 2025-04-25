from flask import Blueprint


web_vendor_bp = Blueprint(
    'web_vendor',
    __name__,
    template_folder='templates',
    static_folder='static'
)

@web_vendor_bp.route('/vendors')
def vendors():
    return "Hello from Vendors!"


@web_vendor_bp.route('/vendors/<vendor_id>')
def vendor(vendor_id):
    return f"Hello from Vendor {vendor_id}!"
