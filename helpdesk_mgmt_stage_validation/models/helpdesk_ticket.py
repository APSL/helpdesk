# Copyright 2022 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import ValidationError


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    @api.depends("team_id")
    def _compute_stage_id(self):
        # This compute is executed on user change, even if not changing team, so let's
        # apply a preventive check for not changing stage if the current one is still
        # applicable to the current team
        for ticket in self:
            applicable_stages = ticket.team_id._get_applicable_stages()
            if ticket.stage_id not in applicable_stages:
                ticket.stage_id = applicable_stages[:1]

    @api.depends("team_id")
    def _compute_user_id(self):
        for ticket in self:
            if ticket.team_id and ticket.user_id not in ticket.team_id.user_ids:
                # If the user is not part of the team, we remove the user
                ticket.user_id = False

    @api.depends("user_id")
    def _compute_team_id(self):
        for ticket in self:
            if not ticket.team_id and ticket.user_id.helpdesk_team_ids:
                # If no team is set, we default to the user's first team
                ticket.team_id = ticket.user_id.helpdesk_team_ids[0]

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        """Show always the stages without team, or stages of the default team."""
        search_domain = [
            "|",
            ("id", "in", stages.ids),
            ("team_ids", "=", False),
        ]
        default_team_id = self.default_get(["team_id"])
        if default_team_id:
            search_domain = [
                "|",
                ("team_ids", "=", default_team_id["team_id"]),
            ] + search_domain
        return stages.search(search_domain, order=order)

    number = fields.Char(string="Ticket number", default="/", readonly=True)
    name = fields.Char(string="Title", required=True)
    description = fields.Html(required=True, sanitize_style=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigned user",
        tracking=True,
        index=True,
        compute="_compute_user_id",
        store=True,
        readonly=False,
        domain="team_id and [('share', '=', False),('id', 'in', user_ids)] or [('share', '=', False)]",  # noqa: B950,E501
    )
    user_ids = fields.Many2many(
        comodel_name="res.users", related="team_id.user_ids", string="Users"
    )
    stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Stage",
        compute="_compute_stage_id",
        store=True,
        readonly=False,
        ondelete="restrict",
        tracking=True,
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        domain="['|',('team_ids', '=', team_id),('team_ids','=',False)]",
    )
    partner_id = fields.Many2one(comodel_name="res.partner", string="Contact")
    commercial_partner_id = fields.Many2one(
        string="Commercial Partner",
        store=True,
        related="partner_id.commercial_partner_id",
    )
    partner_name = fields.Char()
    partner_email = fields.Char(string="Email")
    last_stage_update = fields.Datetime(default=fields.Datetime.now)
    assigned_date = fields.Datetime()
    closed_date = fields.Datetime()
    closed = fields.Boolean(related="stage_id.closed")
    unattended = fields.Boolean(related="stage_id.unattended", store=True)
    tag_ids = fields.Many2many(comodel_name="helpdesk.ticket.tag", string="Tags")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    channel_id = fields.Many2one(
        comodel_name="helpdesk.ticket.channel",
        string="Channel",
        help="Channel indicates where the source of a ticket"
        "comes from (it could be a phone call, an email...)",
    )
    category_id = fields.Many2one(
        comodel_name="helpdesk.ticket.category",
        string="Category",
    )
    team_id = fields.Many2one(
        comodel_name="helpdesk.ticket.team",
        string="Team",
        index=True,
        compute="_compute_team_id",
        store=True,
        readonly=False,
    )
    priority = fields.Selection(
        selection=[
            ("0", "Low"),
            ("1", "Medium"),
            ("2", "High"),
            ("3", "Very High"),
        ],
        default="1",
    )
    attachment_ids = fields.One2many(
        comodel_name="ir.attachment",
        inverse_name="res_id",
        domain=[("res_model", "=", "helpdesk.ticket")],
        string="Media Attachments",
    )
    color = fields.Integer(string="Color Index")
    kanban_state = fields.Selection(
        selection=[
            ("normal", "Default"),
            ("done", "Ready for next stage"),
            ("blocked", "Blocked"),
        ],
    )
    sequence = fields.Integer(
        index=True,
        default=10,
        help="Gives the sequence order when displaying a list of tickets.",
    )
    active = fields.Boolean(default=True)

    @api.model
    def default_get(self, fields):
        # The appropriate user is defined only if the "Auto assign User" option is
        # checked in the company.
        # If the team is set, the user must belong to that team.
        defaults = super().default_get(fields)
        company_id = defaults.get("company_id") or self.env.company.id
        if "user_id" in fields and not defaults.get("user_id"):
            company = self.env["res.company"].browse(company_id)
            if company.helpdesk_mgmt_ticket_auto_assign:
                if defaults.get("team_id"):
                    team = self.env["helpdesk.ticket.team"].browse(
                        defaults.get("team_id")
                    )
                    if self.env.user in team.user_ids:
                        defaults["user_id"] = self.env.user.id
                else:
                    defaults["user_id"] = self.env.user.id
        return defaults

    @api.depends("name")
    def _compute_display_name(self):
        for ticket in self:
            ticket.display_name = f"{ticket.number} - {ticket.name}"

    def assign_to_me(self):
        self.write({"user_id": self.env.user.id})

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            self.partner_name = self.partner_id.name
            self.partner_email = self.partner_id.email

    # ---------------------------------------------------
    # CRUD
    # ---------------------------------------------------

    def _creation_subtype(self):
        return self.env.ref("helpdesk_mgmt.hlp_tck_created")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("number", "/") == "/":
                vals["number"] = self._prepare_ticket_number(vals)
            if vals.get("user_id") and not vals.get("assigned_date"):
                vals["assigned_date"] = fields.Datetime.now()
            if vals.get("team_id"):
                team = self.env["helpdesk.ticket.team"].browse([vals["team_id"]])
                if team.company_id:
                    vals["company_id"] = team.company_id.id
                if "stage_id" not in vals:
                    # Ensure that stage_id is set before creating the ticket
                    # so that the field is tracked correctly
                    # and notifications can be sent by email
                    # if a mail template is configured
                    vals["stage_id"] = team._get_applicable_stages()[:1].id
            # Automatically set default e-mail channel when created from the
            # fetchmail cron task
            if self.env.context.get("fetchmail_cron_running") and not vals.get(
                "channel_id"
            ):
                channel_email_id = self.env.ref(
                    "helpdesk_mgmt.helpdesk_ticket_channel_email",
                    raise_if_not_found=False,
                )
                if channel_email_id:
                    vals["channel_id"] = channel_email_id.id
        return super().create(vals_list)

    def copy(self, default=None):
        self.ensure_one()
        error_message = False
        field_ids = self.stage_id.sudo().validate_field_ids
        if field_ids:
            # Otherwise `self.read([])` reads all the fields
            # and the user might not have enough access rights to read them
            field_names = [x.name for x in field_ids]
            values = self.read(field_names)
            labels = self.fields_get(field_names, attributes=["string"])
            fields = [
                labels[field.name]["string"]
                for field in field_ids
                if not values[0][field.name]
            ]
            fields = ", ".join(fields)
            if fields:
                error_message = _(
                    "Ticket %(ticket)s can't be moved to the stage %(stage)s until "
                    "the following fields are set: %(fields)s.",
                    ticket=self.name,
                    stage=self.stage_id.name,
                    fields=fields,
                )
        return error_message

    def _validate_stage_fields_error_message(self):
        error_message = []
        for record in self:
            message = record._check_ticket_has_empty_fields()
            if message:
                error_message.append(message)
        return error_message

    @api.constrains("stage_id")
    def _validate_stage_fields(self):
        message = self._validate_stage_fields_error_message()
        if message:
            message = "\n".join(message)
            raise ValidationError(message)
