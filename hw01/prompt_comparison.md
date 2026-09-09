# Prompt Comparison
## Strategy 1
### Prompt
What are the most important metrics for measuring credit risk in a consumer loan portfolio?
### First 150 Words
The metrics that matter most for consumer loan credit risk generally fall into a few categories, and most portfolio teams track all of them together rather than picking just one. Delinquency and default measures are the foundation. Days-past-due (DPD) buckets — typically 30, 60, 90+ — show early warning signs before a loan actually charges off. From there, the net charge-off rate (charge-offs minus recoveries, as a percentage of average outstanding balance) is probably the single most-watched credit risk metric, since it reflects realized losses. Non-performing loan (NPL) ratio — balances 90+ days past due or in nonaccrual status, divided by total loans — is the standard snapshot of portfolio health at a point in time. Roll rate analysis tracks the probability that a loan in one delinquency bucket moves to the next (e.g., what percent of 30-day-past-due loans roll to 60-day). This is especially useful for early detection since
### Evaluation
Claude gave a pretty good surface-level overview on prevalent credit-risk metrics, and didn't overcomplicate the explanations of these metrics. However, the response Claude provided was relatively generic, and it wasn't very tailored towards Wildcat's data. It focused more on what lenders what track in a general scenario. Additionally, my prompt asked for the most important metrics, and I feel that Claude wasn't concise in identifying the metrics in a clear-cut manner.
## Strategy 2
### Prompt
You are a consumer lending manager who is lecturing as a guest speaker for students in a business analytics class. A student asks you the following question: "What are the most important metrics for measuring credit risk in a consumer loan portfolio?"
### First 150 Words
Great question — and one every lending shop lives and dies by. If you're only going to walk away with a handful of metrics, here's how I'd group them. The first bucket is delinquency and default metrics, which tell you who's not paying. The classic ones are 30/60/90+ days-past-due (DPD) rates, which act as early warning signals — 30 DPD tells you who's starting to struggle, while 90+ DPD is a much stronger signal of real trouble. From there you move to the charge-off rate, usually expressed as net charge-offs as a percentage of average outstanding balances, which captures losses you've actually realized rather than just flagged. A related one is the non-performing loan (NPL) ratio — the share of the portfolio that's stopped performing altogether. I'd also mention roll rate analysis, which tracks how loans migrate between delinquency buckets month over month (what percentage of 30-DPD accounts "roll" to
### Evaluation
This response was slightly stronger than the first response, and it seems that the role I assigned Claude provided a more practical and organized answer. Claude organized the response into four buckets, and was able to identify delinquency, roll rates, credit scores, DTI, and charge-offs as the important metrics. 
## Strategy 3
### Prompt
This is the format I would like you to use:
1. State the metrics
2. Explain why the metric is important
3. Provide a short example that conceptualizes the reasoning.
   
Here is my question: "What are the most important metrics for measuring credit risk in a consumer loan portfolio?"
### First 150 Words
"Delinquency Rate (30/60/90+ Days Past Due) 

This is the earliest warning signal in the portfolio. It measures the percentage of loans falling behind on payments before they escalate to default or charge-off. Because it's a leading indicator, it lets you catch deterioration in underwriting or economic stress well before losses show up on the P&L. Example: If your Auto and Personal loan buckets show a jump in 30-day delinquencies quarter-over-quarter while Business and Education stay flat, that's a signal to dig into recent underwriting vintages or borrower segments in those two categories before the problem compounds into 90+ day delinquency or charge-offs. 

Net Charge-Off Rate

This measures the dollar amount of loans written off as uncollectible, net of recoveries, as a percentage of average outstanding balances. It's the most direct, realized measure of credit losses and is what ultimately hits the income statement, so it's the metric most closely tied"
### Evaluation
Claude's response with the third strategy was very straightforward and tailored to the format that I wanted it to use. Compared to the first two strategies, there was more connection to Wildcat's data, specifically the five loan purposes, in this response. On the contrary, I would have liked more direct focus on the other key variables as well (credit score, DTI, annual income). Some explanations strayed beyond that, and the answers were a lot more advanced than they had to be.
### Conclusion
The third strategy is best fit for a BI analyst because the format is the easiest to follow. Claude strictly followed to metric, explanation, and example format, so it would be easier for a BI analyst to gather insights from this. The third strategy also made better connections to Wildcat's data without providing too generic of a response. In comparison, the first strategy stuck solely to surface-level responses and did not draw specific examples from the dataset. Additionally, while the second strategy was able to create a stronger, more organized response with a role assigned, the third strategy overall had the strongest, most organized answer. 
## Fact Check
### Claude's Claim
"Net Charge-Off Rate

This measures the dollar amount of loans written off as uncollectible, net of recoveries, as a percentage of average outstanding balances. It's the most direct, realized measure of credit losses and is what ultimately hits the income statement, so it's the metric most closely tied to profitability of the lending book. "
### Source
Source: Investopedia

URL: https://www.investopedia.com/terms/n/net-charge-off-rate.asp
### Source's Definition
The net charge-off rate measures the proportion of debt owed to a company that is unlikely to be paid back to that company. This "bad debt" will then be written off on its financial statements. NCO rates shed important information to investors and analysts about the credit standards of lenders and the quality of their loan portfolio, and may also provide signals about general economic conditions.
### Claude vs. Investopedia
Based on the core claim both Claude and Investopedia make, I would say that they both agree on the central core claim that net charge-off rate can indicate the quality of the portfolio and that the debt will most likely not be recovered. However, when it comes to Claude's judgment, I interpreted Claude's statement that this metric is most related to profitability to be more of an interpretation rather than a simple definition. Overall, Claude's definition was reliable, but it doesn't hurt to fact check for biases or judgment pieces surrounding that definition.
