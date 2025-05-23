# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies.

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

class StockRevaluation(models.TransientModel):
    _inherit = 'stock.valuation.layer.revaluation'

    warehouse_id = fields.Many2one("stock.warehouse",string="Warehouse",required=True)


    def action_validate_revaluation(self):
        """ Revaluate the stock for `self.product_id` in `self.company_id`.

        - Change the stardard price with the new valuation by product unit.
        - Create a manual stock valuation layer with the `added_value` of `self`.
        - Distribute the `added_value` on the remaining_value of layers still in stock (with a remaining quantity)
        - If the Inventory Valuation of the product category is automated, create
        related account move.
        """
        self.ensure_one()
        if self.currency_id.is_zero(self.added_value):
            raise UserError(_("The added value doesn't have any impact on the stock valuation"))

        product_id = self.product_id.with_company(self.company_id)

        remaining_svls = self.env['stock.valuation.layer'].search([
            ('product_id', '=', product_id.id),
            ('remaining_qty', '>', 0),
            ('company_id', '=', self.company_id.id),
        ])

        # Create a manual stock valuation layer
        if self.reason:
            description = _("Manual Stock Valuation: %s.", self.reason)
        else:
            description = _("Manual Stock Valuation: No Reason Given.")
        if product_id.categ_id.property_cost_method == 'average':
            description += _(
                " Product cost updated from %(previous)s to %(new_cost)s.",
                previous=product_id.standard_price,
                new_cost=product_id.standard_price + self.added_value / self.current_quantity_svl
            )
        revaluation_svl_vals = {
            'company_id': self.company_id.id,
            'product_id': product_id.id,
            'description': description,
            'value': self.added_value,
            'quantity': 0,
            'warehouse_id' : self.warehouse_id.id,
        }

        remaining_qty = sum(remaining_svls.mapped('remaining_qty'))
        remaining_value = self.added_value
        remaining_value_unit_cost = self.currency_id.round(remaining_value / remaining_qty)
        for svl in remaining_svls:
            if float_is_zero(svl.remaining_qty - remaining_qty, precision_rounding=self.product_id.uom_id.rounding):
                svl.remaining_value += remaining_value
            else:
                taken_remaining_value = remaining_value_unit_cost * svl.remaining_qty
                svl.remaining_value += taken_remaining_value
                remaining_value -= taken_remaining_value
                remaining_qty -= svl.remaining_qty

        revaluation_svl = self.env['stock.valuation.layer'].create(revaluation_svl_vals)

        # Update the stardard price in case of AVCO
        if product_id.categ_id.property_cost_method == 'average':
            product_id.with_context(disable_auto_svl=True).standard_price += self.added_value / self.current_quantity_svl

            # Update Average Costing
            warehouse = product_id.warehouse_cost_lines.filtered(
                lambda x: x.warehouse_id.id == self.warehouse_id.id)
            if self.warehouse_id and not warehouse:
                raise UserError(_("Selected Warehouse has no stock for this Product"))
            warehouse_cost = warehouse.cost
            group_quantity = self.get_quantity_from_valuation(
                product_id.id, self.warehouse_id.id)
            if group_quantity > 0:
                warehouse_cost += self.added_value/group_quantity
            else:
                warehouse_cost += self.added_value
            warehouse.write({
                'cost' : warehouse_cost
            })

        # If the Inventory Valuation of the product category is automated, create related account move.
        if self.property_valuation != 'real_time':
            return True

        accounts = product_id.product_tmpl_id.get_product_accounts()

        if self.added_value < 0:
            debit_account_id = self.account_id.id
            credit_account_id = accounts.get('stock_valuation') and accounts['stock_valuation'].id
        else:
            debit_account_id = accounts.get('stock_valuation') and accounts['stock_valuation'].id
            credit_account_id = self.account_id.id

        move_vals = {
            'journal_id': self.account_journal_id.id or accounts['stock_journal'].id,
            'company_id': self.company_id.id,
            'ref': _("Revaluation of %s", product_id.display_name),
            'stock_valuation_layer_ids': [(6, None, [revaluation_svl.id])],
            'date': self.date or fields.Date.today(),
            'move_type': 'entry',
            'line_ids': [(0, 0, {
                'name': _('%(user)s changed stock valuation from  %(previous)s to %(new_value)s - %(product)s',
                    user=self.env.user.name,
                    previous=self.current_value_svl,
                    new_value=self.current_value_svl + self.added_value,
                    product=product_id.display_name,
                ),
                'account_id': debit_account_id,
                'debit': abs(self.added_value),
                'credit': 0,
                'product_id': product_id.id,
            }), (0, 0, {
                'name': _('%(user)s changed stock valuation from  %(previous)s to %(new_value)s - %(product)s',
                    user=self.env.user.name,
                    previous=self.current_value_svl,
                    new_value=self.current_value_svl + self.added_value,
                    product=product_id.display_name,
                ),
                'account_id': credit_account_id,
                'debit': 0,
                'credit': abs(self.added_value),
                'product_id': product_id.id,
            })],
        }
        account_move = self.env['account.move'].create(move_vals)
        account_move._post()

        return True

    def get_quantity_from_valuation(self, productid, warehouseid):
        # * Softhealer code Start *

        #   New Methpd (accepts the product and warehouse and returns the quantity warehouse wise drom svl)

        # * Softhealer code end *
        company_id = self.env.company.id
        domain = [
            ('product_id', '=', productid),
            ('company_id', '=', company_id),
            ('warehouse_id', '=', warehouseid)
        ]
        if self.env.context.get('to_date'):
            to_date = fields.Datetime.to_datetime(self.env.context['to_date'])
            domain.append(('create_date', '<=', to_date))
        groups = self.env['stock.valuation.layer'].read_group(
            domain, ['value:sum', 'quantity:sum'], ['product_id'])
        if groups:
            for group in groups:
                return group['quantity']
        else:
            return 0
