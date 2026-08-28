"""Concept boards, written by hand from sources that were checked.

Each note is: a precise definition with citations, then four restatements of it
— plain English, Hindi, where it shows up, and a shape. Only the definition
asserts anything; the rest say the same thing differently, which is why they are
allowed to exist without their own sources.

The `application` layer is the one worth writing carefully. It is where the
honest limit goes — the thing an interviewer probes for after the definition
comes out clean. A concept explained without its failure mode is a concept
half-learned.
"""
from __future__ import annotations

W = "https://en.wikipedia.org/wiki/"
IBM = "https://www.ibm.com/think/topics/"


def note(term, definition, sources, simple, hindi, application, visual):
    return dict(term=term, definition=definition, sources=sources, simple=simple,
                hindi=hindi, application=application, visual=visual)


AI = [
    note(
        "Prompt engineering",
        "Prompt engineering is the practice of shaping a model's input — instructions, "
        "examples, formatting and constraints — to get reliable output, without changing "
        "the model's weights. It is the cheapest of the three ways to change behaviour, "
        "and the only one that is reversible in a single edit.",
        [IBM + "prompt-engineering", W + "Prompt_engineering"],
        "Asking well. The model does not read your mind, and most of what looks like a "
        "capability gap is a question that was never made specific enough.",
        "सही तरीक़े से पूछना। मॉडल आपका मन नहीं पढ़ सकता — जो अक्सर मॉडल की कमी लगती है, "
        "वह असल में सवाल का ठीक से न पूछा जाना होता है।",
        "Try it before retrieval and long before fine-tuning. The limit worth knowing: a "
        "prompt cannot supply a fact the model was never given, so if the answer needs "
        "current or private information, no amount of rewording will produce it.",
        dict(kind="layers", caption="Three ways to change behaviour, cheapest first",
             nodes=[{"label": "Prompt", "note": "change the input — instant, reversible"},
                    {"label": "Retrieval", "note": "change what it can see"},
                    {"label": "Fine-tune", "note": "change the weights — slow, sticky"}]),
    ),
    note(
        "Context window",
        "The context window is the maximum number of tokens a model can attend to at once "
        "— the prompt and the response together. Everything the model 'knows' during a "
        "single call is either in its weights or inside that window; nothing else exists "
        "to it.",
        [W + "Large_language_model", IBM + "context-window"],
        "The model's desk. Anything on the desk it can use; anything off the desk may as "
        "well not exist. A bigger desk costs more and does not make it read more carefully.",
        "मॉडल की मेज़। जो मेज़ पर है वही वह इस्तेमाल कर सकता है; बाक़ी सब उसके लिए है ही नहीं। "
        "बड़ी मेज़ महँगी पड़ती है, और इससे वह ज़्यादा ध्यान से नहीं पढ़ता।",
        "It sets what you can stuff into a prompt, and it is why retrieval exists at all. "
        "The failure that surprises people: filling the window does not improve answers "
        "and often degrades them, because relevant material gets diluted by everything "
        "else competing for attention.",
        dict(kind="compare", caption="Two kinds of knowing",
             nodes=[{"label": "Weights", "note": "learned in training, fixed, uncited"},
                    {"label": "Context", "note": "supplied now, exact, temporary"},
                    {"label": "Neither", "note": "does not exist to the model"}]),
    ),
    note(
        "Vector database",
        "A vector database stores embeddings and answers nearest-neighbour queries over "
        "them, usually with an approximate index so search stays fast as the collection "
        "grows. It is the storage half of semantic search: the model turns text into "
        "vectors, and this finds the close ones.",
        [IBM + "vector-database", W + "Vector_database"],
        "A filing cabinet organised by meaning rather than by label. You hand it a "
        "sentence and it hands back the things that mean something similar.",
        "ऐसी अलमारी जो नाम से नहीं, अर्थ से व्यवस्थित है। आप एक वाक्य देते हैं और वह वे चीज़ें "
        "लौटाती है जिनका मतलब उससे मिलता-जुलता है।",
        "The retrieval half of RAG. Worth knowing that the index is usually approximate — "
        "it trades a small amount of recall for a large amount of speed, so 'the right "
        "document exists and was not returned' is a normal failure rather than a bug.",
        dict(kind="flow", caption="Store vectors, ask for the near ones",
             nodes=[{"label": "Embed", "note": "text becomes a vector"},
                    {"label": "Index", "note": "stored for approximate search"},
                    {"label": "Query", "note": "the question is embedded too"},
                    {"label": "Neighbours", "note": "closest vectors come back"}]),
    ),
    note(
        "AI agent",
        "An agent is a model given tools and a loop: it decides an action, the action is "
        "executed, the result is fed back, and it decides again until the task is done or "
        "it stops. The model still only produces text — what makes it an agent is that "
        "some of that text is interpreted as a call to something real.",
        [IBM + "ai-agents"],
        "A model that can press buttons, not just describe them. The loop is the whole "
        "difference: it sees the result of what it did and decides what to do next.",
        "ऐसा मॉडल जो सिर्फ़ बताता नहीं, बटन दबा भी सकता है। असली फ़र्क़ लूप का है — वह अपने "
        "किए का नतीजा देखता है और फिर तय करता है कि आगे क्या करना है।",
        "Useful when the steps cannot be known in advance. The risk is structural rather "
        "than occasional: a loop that can act can act wrongly many times before anyone "
        "looks, which is why the actions worth guarding get a gate the loop cannot skip.",
        dict(kind="cycle", caption="Decide, act, observe",
             nodes=[{"label": "Decide", "note": "model picks an action"},
                    {"label": "Act", "note": "a tool actually runs"},
                    {"label": "Observe", "note": "the result returns to the model"}]),
    ),
]

ML = [
    note(
        "Overfitting",
        "Overfitting is when a model learns the noise in its training data as if it were "
        "signal, scoring well on data it has seen and poorly on data it has not. It is "
        "diagnosed by the gap between training and held-out performance, not by the "
        "training score alone.",
        [IBM + "overfitting", W + "Overfitting"],
        "Memorising the answers instead of learning the subject. Perfect on the practice "
        "paper, lost in the exam.",
        "विषय समझने के बजाय उत्तर रट लेना। अभ्यास प्रश्नपत्र में पूरे अंक, असली परीक्षा में कुछ नहीं।",
        "The reason you never report a training score. The tell in an interview is being "
        "asked what you did about it: more data, fewer parameters, regularisation, early "
        "stopping — and the honest answer that a validation split is what let you see it "
        "at all.",
        dict(kind="compare", caption="The gap is the diagnosis",
             nodes=[{"label": "Underfit", "note": "poor on both — too simple"},
                    {"label": "Good fit", "note": "similar on both"},
                    {"label": "Overfit", "note": "great on train, poor on held-out"}]),
    ),
    note(
        "Cross-validation",
        "Cross-validation splits the data into k folds, trains on k−1 of them and "
        "evaluates on the one held out, rotating until every fold has been the test set. "
        "The averaged score is a less luck-dependent estimate of performance than a single "
        "train/test split.",
        [W + "Cross-validation_(statistics)"],
        "Marking the exam five times with a different question held back each time, then "
        "averaging. One split can flatter you by accident; five is harder to fluke.",
        "पाँच बार जाँचना, हर बार एक अलग हिस्सा छिपाकर, और फिर औसत निकालना। एक बार का नतीजा "
        "संयोग से अच्छा आ सकता है; पाँच बार का नहीं।",
        "Standard whenever data is limited enough that one split is noisy. The trap: with "
        "time-ordered data, random folds let the model train on the future and test on the "
        "past, which produces a wonderful score and a worthless model.",
        dict(kind="cycle", caption="Every fold takes a turn as the test set",
             nodes=[{"label": "Split into k", "note": "equal folds"},
                    {"label": "Hold one out", "note": "train on the rest"},
                    {"label": "Score", "note": "on the held-out fold"},
                    {"label": "Rotate", "note": "until all have been held out"}]),
    ),
    note(
        "Bias–variance tradeoff",
        "Expected error decomposes into bias — error from a model too simple to capture "
        "the pattern — and variance — error from a model so flexible it changes a great "
        "deal with the training sample. Reducing one typically raises the other, so the "
        "goal is the balance rather than the minimum of either.",
        [W + "Bias%E2%80%93variance_tradeoff"],
        "Too rigid and you miss the shape; too flexible and you chase every wobble. Skill "
        "is knowing which of the two you currently have.",
        "बहुत सख़्त मॉडल असली पैटर्न पकड़ ही नहीं पाता; बहुत लचीला हर छोटी हलचल के पीछे भाग "
        "जाता है। असली समझदारी यह जानने में है कि अभी कौन-सी समस्या है।",
        "It is the frame behind almost every modelling decision — model size, "
        "regularisation strength, tree depth. In an interview it usually arrives as 'your "
        "model underperforms, what do you check', and the answer is which of the two the "
        "train/validation gap points at.",
        dict(kind="compare", caption="Which error are you paying?",
             nodes=[{"label": "High bias", "note": "too simple, wrong everywhere"},
                    {"label": "Balanced", "note": "the target"},
                    {"label": "High variance", "note": "too flexible, unstable"}]),
    ),
    note(
        "Precision and recall",
        "Precision is the share of predicted positives that were right; recall is the "
        "share of actual positives that were found. They trade off against each other as "
        "the decision threshold moves, and F1 is their harmonic mean.",
        [IBM + "precision-and-recall", W + "Precision_and_recall"],
        "Precision: when it says yes, is it right? Recall: of the ones that mattered, how "
        "many did it catch? You can nearly always buy one with the other.",
        "प्रिसिज़न: जब यह 'हाँ' कहता है, तो कितनी बार सही होता है? रिकॉल: जितने असल मामले थे, "
        "उनमें से कितने पकड़ में आए? एक को बढ़ाना अक्सर दूसरे की क़ीमत पर होता है।",
        "Which one matters is a business question, not a modelling one. Fraud screening "
        "wants recall and accepts false alarms; an auto-reject rule wants precision "
        "because every false positive is a real customer turned away. Accuracy hides both, "
        "which is why it is the wrong headline on imbalanced data.",
        dict(kind="compare", caption="Two questions, one threshold",
             nodes=[{"label": "Precision", "note": "of what it flagged, how much was real"},
                    {"label": "Recall", "note": "of what was real, how much it flagged"},
                    {"label": "Threshold", "note": "moving it trades one for the other"}]),
    ),
    note(
        "Feature engineering",
        "Feature engineering is constructing the inputs a model sees — deriving ratios, "
        "aggregates, time-since values and encodings from raw fields — so the pattern is "
        "expressible in the form the model can use.",
        [W + "Feature_engineering"],
        "Doing some of the thinking before the model has to. 'Date of birth' is hard to "
        "learn from; 'age' is easy, and it is the same fact.",
        "मॉडल के लिए कुछ सोच पहले ही कर देना। 'जन्म तिथि' से सीखना कठिन है, 'उम्र' से आसान — "
        "जबकि जानकारी वही है।",
        "Usually where the gains actually are on tabular problems, more than model choice. "
        "The danger to name before someone names it for you is leakage: a feature computed "
        "using information that would not exist at prediction time scores brilliantly in "
        "testing and fails completely in production.",
        dict(kind="flow", caption="Raw fields become usable signal",
             nodes=[{"label": "Raw", "note": "timestamps, ids, free text"},
                    {"label": "Derive", "note": "ratios, aggregates, recency"},
                    {"label": "Encode", "note": "into a form the model accepts"},
                    {"label": "Check leakage", "note": "would this exist at predict time?"}]),
    ),
]

STATS = [
    note(
        "p-value",
        "A p-value is the probability of observing a result at least as extreme as the one "
        "measured, assuming the null hypothesis is true. It is a statement about data "
        "given a hypothesis, not about the hypothesis given the data.",
        [W + "P-value"],
        "How surprising this result would be if nothing were really going on. Small means "
        "surprising. It does not tell you the chance that you are right.",
        "अगर असल में कुछ हो ही नहीं रहा, तो यह नतीजा कितना चौंकाने वाला होता — p-value यही "
        "बताता है। छोटा मतलब चौंकाने वाला। यह नहीं बताता कि आप सही हैं या नहीं।",
        "The misreading is the interview question. 'p = 0.03 means a 3% chance the result "
        "is wrong' is false, and so is treating 0.049 and 0.051 as different findings. It "
        "also says nothing about effect size — a trivial difference becomes significant "
        "with a large enough sample.",
        dict(kind="compare", caption="What it does and does not say",
             nodes=[{"label": "It says", "note": "how odd this data is under the null"},
                    {"label": "It does not say", "note": "the probability you are right"},
                    {"label": "It ignores", "note": "whether the effect is big enough to matter"}]),
    ),
    note(
        "Confidence interval",
        "A confidence interval is a range computed so that, under repeated sampling, a "
        "stated proportion of such intervals would contain the true parameter. A 95% "
        "interval refers to the procedure's long-run behaviour, not to a probability about "
        "the particular interval in front of you.",
        [W + "Confidence_interval"],
        "A range with its uncertainty shown. Far more useful than a single number, because "
        "it says how much the number could move.",
        "एक सीमा, जिसमें अनिश्चितता भी दिखती है। अकेले आँकड़े से कहीं बेहतर, क्योंकि यह बताती है "
        "कि वह आँकड़ा कितना ऊपर-नीचे हो सकता है।",
        "Report one wherever you report an estimate. It is also the cleanest way to say "
        "'we cannot tell yet': an interval spanning zero means the data do not settle the "
        "direction, which is a real finding and usually a more honest one than a p-value.",
        dict(kind="flow", caption="From sample to a range",
             nodes=[{"label": "Sample", "note": "one draw from the population"},
                    {"label": "Estimate", "note": "the statistic you measured"},
                    {"label": "Interval", "note": "estimate ± its uncertainty"},
                    {"label": "Repeat", "note": "95% of such intervals would cover the truth"}]),
    ),
    note(
        "A/B test",
        "An A/B test randomly assigns units to a control and one or more variants, so that "
        "the only systematic difference between groups is the treatment. Randomisation is "
        "what licenses a causal claim; without it the comparison is observational.",
        [W + "A/B_testing"],
        "Split people at random, change one thing for one group, and compare. Random "
        "assignment is what lets you say the change caused the difference.",
        "लोगों को बेतरतीब ढंग से दो हिस्सों में बाँटो, एक हिस्से के लिए एक चीज़ बदलो, फिर तुलना करो। "
        "बेतरतीब बँटवारा ही आपको यह कहने का हक़ देता है कि बदलाव ने असर किया।",
        "Decide the metric and the sample size before starting. Two failures worth naming: "
        "peeking — stopping the moment it looks significant, which inflates false positives "
        "— and running many variants without correcting for the fact that testing twenty "
        "things means one will look significant by chance.",
        dict(kind="flow", caption="Randomise, expose, compare",
             nodes=[{"label": "Randomise", "note": "assignment is the whole point"},
                    {"label": "Expose", "note": "one group sees the change"},
                    {"label": "Measure", "note": "the metric fixed in advance"},
                    {"label": "Compare", "note": "difference, with its interval"}]),
    ),
    note(
        "Correlation is not causation",
        "Two variables moving together can arise from one causing the other, from reverse "
        "causation, from a common cause, or from selection in how the data were collected. "
        "Establishing which requires an intervention or an identification strategy, not a "
        "stronger correlation.",
        [W + "Correlation_does_not_imply_causation"],
        "Ice cream sales and drownings rise together. Neither causes the other — summer "
        "causes both.",
        "आइसक्रीम की बिक्री और डूबने की घटनाएँ साथ-साथ बढ़ती हैं। एक दूसरे का कारण नहीं है — "
        "गर्मी का मौसम दोनों का कारण है।",
        "The most common way an analysis is wrong while being technically correct. Say out "
        "loud which confounders you considered, and prefer 'associated with' unless the "
        "design supports more — the honesty is usually the thing being tested.",
        dict(kind="compare", caption="Four reasons two things move together",
             nodes=[{"label": "A causes B", "note": "the assumed one"},
                    {"label": "B causes A", "note": "reverse"},
                    {"label": "C causes both", "note": "confounding"},
                    {"label": "Selection", "note": "how the data were gathered"}]),
    ),
    note(
        "Simpson's paradox",
        "Simpson's paradox is when a trend visible in each subgroup reverses once the "
        "subgroups are combined, because group sizes and base rates differ. Which "
        "aggregation is correct depends on the causal structure, not on the arithmetic.",
        [W + "Simpson%27s_paradox"],
        "Every department admits women at a higher rate, but the university overall admits "
        "men at a higher rate — because women applied more to the competitive departments.",
        "हर विभाग में महिलाओं का चयन प्रतिशत ज़्यादा है, फिर भी पूरे विश्वविद्यालय में पुरुषों का "
        "ज़्यादा दिखता है — क्योंकि महिलाओं ने उन विभागों में ज़्यादा आवेदन किया जहाँ मुक़ाबला कठिन था।",
        "Why an aggregate metric can move opposite to every segment inside it. Practically: "
        "when a headline number disagrees with the segments, do not average harder — find "
        "the variable whose mix changed.",
        dict(kind="compare", caption="The mix, not the rates, moved",
             nodes=[{"label": "Group A", "note": "treatment wins"},
                    {"label": "Group B", "note": "treatment wins"},
                    {"label": "Combined", "note": "treatment loses"}]),
    ),
]

DATA = [
    note(
        "ETL and ELT",
        "ETL extracts data, transforms it in a dedicated processing step, and loads the "
        "result. ELT loads the raw data into the destination first and transforms it "
        "there, using the warehouse's own compute. The ordering choice follows where the "
        "cheap compute and the governance requirements sit.",
        [IBM + "etl", W + "Extract,_transform,_load"],
        "Do you clean the ingredients before they go in the fridge, or after you take them "
        "out? Modern warehouses are powerful enough that most people now clean afterwards.",
        "क्या आप सामान फ़्रिज में रखने से पहले साफ़ करते हैं या निकालने के बाद? आजकल के वेयरहाउस "
        "इतने ताक़तवर हैं कि ज़्यादातर लोग बाद में साफ़ करना पसंद करते हैं।",
        "ELT keeps the raw data, so a transformation bug is re-runnable rather than a data "
        "loss. That is usually the deciding argument. ETL still wins where the raw data "
        "must not land in the destination at all — regulated fields being the common case.",
        dict(kind="compare", caption="Where the transform happens",
             nodes=[{"label": "ETL", "note": "transform before load; raw never lands"},
                    {"label": "ELT", "note": "load raw, transform in the warehouse"},
                    {"label": "Consequence", "note": "ELT can re-run; ETL cannot"}]),
    ),
    note(
        "Idempotence",
        "An operation is idempotent when applying it more than once has the same effect as "
        "applying it once. In pipelines it is the property that makes a retry safe: a "
        "re-run produces the same end state rather than duplicating work.",
        [W + "Idempotence"],
        "Pressing the lift button twice does not summon two lifts. A pipeline you can "
        "safely run again after a failure has the same property.",
        "लिफ़्ट का बटन दो बार दबाने से दो लिफ़्ट नहीं आतीं। जिस पाइपलाइन को असफलता के बाद "
        "दोबारा चलाना सुरक्षित हो, उसमें यही गुण होता है।",
        "The single most useful property to design in, because every scheduler retries "
        "eventually. Usually achieved with a natural key and an upsert, or by replacing a "
        "partition wholesale rather than appending to it. Without it, one retry silently "
        "doubles a day's rows.",
        dict(kind="compare", caption="What a retry does",
             nodes=[{"label": "Append", "note": "retry duplicates the day"},
                    {"label": "Upsert by key", "note": "retry overwrites, same result"},
                    {"label": "Replace partition", "note": "retry is a no-op"}]),
    ),
    note(
        "Star schema",
        "A star schema puts measurements in a central fact table, keyed to surrounding "
        "dimension tables that hold descriptive attributes. Dimensions are deliberately "
        "denormalised so a query joins one level out rather than traversing a chain.",
        [W + "Star_schema"],
        "One table of things that happened, surrounded by tables describing who, what and "
        "when. Every question is the middle table joined to a few of the outer ones.",
        "बीच में एक टेबल — क्या हुआ; चारों ओर टेबलें — किसने, क्या, कब। हर सवाल का जवाब बीच वाली "
        "टेबल को कुछ बाहरी टेबलों से जोड़कर मिल जाता है।",
        "The default shape for analytical warehouses, and the vocabulary a data-modelling "
        "interview is conducted in. Get grain out first — 'one row per order line' — "
        "because almost every wrong number in a star schema is a fact table joined at a "
        "grain nobody stated.",
        dict(kind="layers", caption="Facts in the middle, description around",
             nodes=[{"label": "Fact", "note": "one row per event, at a stated grain"},
                    {"label": "Dimensions", "note": "who, what, where, when"},
                    {"label": "Grain", "note": "decide it before joining anything"}]),
    ),
    note(
        "Slowly changing dimension",
        "A slowly changing dimension is a dimension whose attributes change over time, and "
        "the type says what happens to history. Type 1 overwrites; Type 2 adds a new row "
        "with validity dates and keeps the old one.",
        [W + "Slowly_changing_dimension"],
        "A customer moves city. Do you overwrite the old address, or keep both with dates? "
        "Overwriting is simpler and quietly rewrites the past.",
        "एक ग्राहक दूसरे शहर चला गया। पुराना पता मिटा दें, या दोनों तारीख़ों के साथ रखें? मिटाना "
        "आसान है, पर इससे पुराना इतिहास चुपचाप बदल जाता है।",
        "It decides whether last year's report still reproduces. Type 1 makes historical "
        "numbers move when someone edits a record today — which is exactly the bug that "
        "gets noticed in an audit rather than in testing.",
        dict(kind="compare", caption="What happens to the old value",
             nodes=[{"label": "Type 1", "note": "overwrite; history is lost"},
                    {"label": "Type 2", "note": "new row with valid-from and valid-to"},
                    {"label": "Consequence", "note": "only Type 2 reproduces old reports"}]),
    ),
    note(
        "Change data capture",
        "Change data capture identifies and streams the rows that changed in a source "
        "system — typically by reading its transaction log — so downstream systems can be "
        "updated incrementally instead of by repeated full extracts.",
        [W + "Change_data_capture"],
        "Instead of re-copying the whole table every night, listen for what changed and "
        "send only that.",
        "हर रात पूरी टेबल दोबारा कॉपी करने के बजाय, सिर्फ़ यह सुनना कि क्या बदला, और वही भेजना।",
        "How near-real-time warehouses are fed without hammering the source database. Two "
        "things to have an answer for: deletes, which a naive implementation misses "
        "entirely, and ordering, because out-of-order events applied naively leave the "
        "target holding a stale value.",
        dict(kind="flow", caption="Read the log, not the table",
             nodes=[{"label": "Transaction log", "note": "the database already records changes"},
                    {"label": "Capture", "note": "inserts, updates and deletes"},
                    {"label": "Stream", "note": "in order, downstream"},
                    {"label": "Apply", "note": "incrementally, idempotently"}]),
    ),
]

# Terms already written up for a board in an earlier pass. Listed so the board
# keeps them: `save_topic` replaces a topic's whole term list, so a second seed
# run that only knew about the new notes would quietly orphan the old ones —
# their content would still sit in concept_note, reachable from nowhere.
AI_ALREADY_SEEDED = [
    "Large language model", "Attention mechanism", "Retrieval-augmented generation",
    "Hallucination", "Fine-tuning", "Embedding",
]

BOARDS = [
    dict(slug="ai", title="AI and language models", order=1,
         blurb="What an interviewer means when they ask whether you 'work with AI'.",
         notes=AI, also=AI_ALREADY_SEEDED),
    dict(slug="ml", title="Machine learning", order=2,
         blurb="The ideas behind any model you have trained, and the failure each one names.",
         notes=ML),
    dict(slug="stats", title="Statistics for analysts", order=3,
         blurb="The handful of ideas that decide whether an analysis is right or merely tidy.",
         notes=STATS),
    dict(slug="data", title="Data engineering", order=4,
         blurb="Pipelines and warehouse modelling — the vocabulary these interviews run in.",
         notes=DATA),
]
