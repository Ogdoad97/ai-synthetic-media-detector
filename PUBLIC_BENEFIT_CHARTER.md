# Public Benefit Charter

**Project:** AI Synthetic Media Authenticity Detector  
**Status:** Foundational project commitment  
**Scope:** Applies to the maintained upstream project, its documentation, releases, evaluation, and governance.

## Mission
This project exists to help people assess the authenticity and provenance of digital media in ways that support the public interest. It aims to improve informed judgement, media literacy, research, and responsible investigation—not to replace human judgement or present uncertain signals as facts.

The project is openly available for public use and reuse under the repository's MIT licence. Its maintainers welcome legitimate research, accessibility, journalism, education, defensive work, and other beneficial uses.

## Commitments
1. **Scientific honesty.** Distinguish measured results from hypotheses, heuristics, and unfinished work. No accuracy, reliability, or provenance claim should exceed the evidence.
2. **Human judgement.** Detector output is evidence to consider, not a verdict. A score must not be represented as proof that a person lied, that media is fake, or that a particular person created it.
3. **Public benefit and fairness.** Consider foreseeable harms, disparate error rates, accessibility, and the consequences of false positives and false negatives.
4. **Privacy by design.** Minimise collection and retention of submitted media and related identifiers, document data flows, and protect sensitive inputs.
5. **Security and resilience.** Test hostile inputs and network boundaries, handle failures safely, and document material security limitations.
6. **Transparency.** Publish evaluation methods, dataset provenance and licences, known limitations, and material changes when safe and lawful.
7. **Responsible contribution.** Changes affecting detection claims, privacy, safety, or security require appropriate tests and review. Contributors should explain the intended benefit and relevant risks.
8. **Continuity.** Keep these commitments in the repository, reference them from the README and contribution guidance, and consider them in release reviews.
9. **Open reuse.** This charter does not override the MIT licence. Downstream users may modify their copies; this charter describes the maintained upstream project's commitments and cannot guarantee how every fork will behave.

## What this project does not promise
- It does not guarantee that all generated media can be detected.
- It does not authenticate a person, establish intent, or prove media origin from a heuristic score.
- It does not guarantee that downstream forks or deployments will preserve safeguards.
- It does not replace expert review, provenance evidence, or independent corroboration.

## Decision rule
When a change creates a material trade-off between performance, openness, privacy, safety, and fairness, document the trade-off, evidence, alternatives, and residual risks. Prefer the least restrictive measure that meaningfully reduces the identified harm while preserving legitimate public-interest use.

## Stewardship
Maintainers should review this charter at least annually and after a material incident or major scope change. Proposed amendments should be discussed openly in project issues or pull requests. Any statement of compliance must be supported by implementation and test evidence, not this document alone.

## Current implementation status
This charter is a governance commitment, not proof that every commitment is implemented. Concrete engineering requirements and verification status are tracked in [Safety and Trust Requirements](docs/SAFETY_AND_TRUST_REQUIREMENTS.md).