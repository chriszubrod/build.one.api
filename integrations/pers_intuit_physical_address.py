
import pyodbc

from . import database_pers


def create_physical_address(id, postal_code, city, country, line_one, country_sub_division_code, company_info_id):
    resp = {}
    sql = (
        '''
        INSERT INTO intuit.PhysicalAddress (Id, PostalCode, City, Country, Line1, CountrySubDivisionCode, CompanyInfoId)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, id, postal_code, city, country, line_one, country_sub_division_code, company_info_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Company Address has been successfully created.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Company Address has NOT been successfully created.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp


def read_physical_address_by_id_and_company_id(id, company_id):
    resp = {}
    sql = (
        '''
        SELECT *
        FROM intuit.PhysicalAddress
        WHERE Id=? AND CompanyInfoId=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        crsr = cnxn.cursor()
        row = crsr.execute(sql, id, company_id).fetchone()
        if row:
            resp = {
                "message": row,
                "rowcount": len(row),
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Company Address was not found.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 500
        }
    finally:
        return resp


def update_physical_address_by_id_and_company_id(id, postal_code, city, country, line_one, country_sub_division_code, company_info_id):
    resp = {}
    sql = (
        '''
        UPDATE intuit.PhysicalAddress
        SET PostalCode=?, City=?, Country=?, Line1=?, CountrySubDivisionCode=?
        WHERE Id=? AND CompanyInfoId=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, postal_code, city, country, line_one, country_sub_division_code, id, company_info_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Company Adddress has been successfully updated.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Company Address has NOT been successfully updated.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp
