# Initial HBM validation

This complete run passed arithmetic and protocol checks. Its INFO latency string
reported countdown draws rather than physical handshake delays. The final run
corrects only that description and repeats the complete workflow. The original
model-server source is retained here to explain its earlier source hash; the
final source and result are in the sibling `hbm` evidence directory. The
comparator correctly rejects this earlier run against the later source.
