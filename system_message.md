# Arepa Lab agent instructions

The canonical instructions are the MCP prompt **`agent_instructions`** in
`backend/mcp_server.py`. Fetch that prompt at startup, as the example in
`test-agents/agent.py` does, instead of maintaining a second system prompt.

Before asking the customer to choose an arepa, the agent calls `list_arepas()`
and shows every returned item's name, description, price in USD and returned
`stock_label` in a numbered list. Descriptions come from the tool response;
the agent must not invent ingredients. If a description is empty, it says
that no description was provided. Menu presentation is guided by the server
prompt, not a separate client-side menu renderer. Clients that fetch the
prompt at startup need a new session to pick up updated instructions.

The seven tools are:

- `register_customer(first_name, last_name, city)`
- `get_customer(customer_id)`
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
