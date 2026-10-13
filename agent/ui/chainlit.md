# Olist warehouse agent

Ask about orders, revenue, sellers, deliveries, payments, and reviews in the Olist Brazilian e-commerce data (2016 to 2018).

- Questions become SQL over the gold star schema, checked by a SQL parser and run by a read-only service account with a byte cap.
- Each query appears as a step while the agent works.
- Every number in an answer is filled in from the query result, not written by the model.
