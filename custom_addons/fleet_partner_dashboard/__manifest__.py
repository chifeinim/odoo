{
    'name': 'Fleet Partner Dashboard',
    'version': '15.0.1.0.0',
    'summary': 'Manage fleet drivers, orders, issues, and product types',
    'category': 'Fleet',
    'author': 'Your Name or Company',
    'website': 'https://github.com/chifeinim',
    'depends': ['base', 'mail', 'web'],
    'data': [
        'data/sequence.xml',
        'security/ir.model.access.csv',
        'views/fleet_driver_views.xml',
        'views/fleet_order_views.xml',
        'views/fleet_driver_issue_views.xml',
        'views/product_type_views.xml',
        'views/driver_issue_tag_views.xml',
        'views/dashboard_template.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'fleet_partner_dashboard/static/src/js/dashboard/driver_dashboard.js',
            'fleet_partner_dashboard/static/src/js/dashboard/driver_dashboard.xml',
        ],
    },
    'installable': True,
    'application': True,
}
