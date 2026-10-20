# Arepa Lab agent instructions

The canonical instructions are the MCP prompt **`agent_instructions`** in
`backend/mcp_server.py`. Fetch that prompt at startup, as the example in
`test-agents/agent.py` does, instead of maintaining a second system prompt.

The six tools are:

- `register_customer(first_name, last_name, city)`
- `list_arepas()`
- `order_arepa(flavor_id, customer_id, kind, voucher_code)`
- `check_order_status(customer_id)`
- `cancel_order(customer_id, kind)`
- `voucher_status()`

The `flavor_id` is the menu item's `id`, not its display name. That field name
is retained for consistency with existing stored orders.

Both test and real orders require a registered customer and the rotating
**order voucher** shown on the dashboard. This is separate from the optional
**workshop training key**. Test orders complete immediately without consuming
stock. Real orders consume one unit and count toward the customer's lifetime
allowance. Only pending orders can be cancelled through MCP.

Retrieve `welcome_banner` for the terminal greeting. The banner and illustration
are branding, not evidence of the ingredients or availability of any menu item.
