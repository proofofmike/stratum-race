<script setup lang="ts">
</script>

<template>
  <div class="about-view">
    <article class="about-content">
      <h1>About StratumRace</h1>

      <section class="about-section">
        <h2>What We Measure</h2>
        <p>
          StratumRace measures how quickly Bitcoin mining pools broadcast new block templates
          to their miners after a block is found on the network. When a new Bitcoin block is
          discovered, every pool must notify its miners to start working on the next block.
          The speed of this notification directly impacts mining efficiency.
        </p>
        <p>
          We connect to dozens of mining pools from multiple geographic vantage points and
          precisely time when each collector receives the pool's <code>mining.notify</code>
          message via the Stratum protocol. The first pool to deliver a full block template
          at a given vantage wins that race. All other pools' times are measured as offsets
          from the winner.
        </p>
      </section>

      <section class="about-section">
        <h2>Why It Matters</h2>
        <p>
          Block notification speed has real economic consequences for miners. During the delay
          between a new block being found and a pool's notification arriving, miners relying on
          that pool's Stratum notification continue working on the previous (now stale) block
          template, wasting electricity and hashrate.
        </p>
        <p>
          Faster notification reduces avoidable work on stale templates and is one component of
          overall pool performance, alongside fees, reliability, template construction quality,
          and payout structure. StratumRace provides transparent, independent measurements to
          help miners make informed decisions about this factor.
        </p>
      </section>

      <section class="about-section">
        <h2>How It Works</h2>
        <ul>
          <li><strong>Collectors</strong> - Lightweight measurement nodes deployed in multiple
            geographic locations connect to each pool's Stratum endpoint and listen for block
            template notifications.</li>
          <li><strong>Race Detection</strong> - When a new block's <code>prevhash</code>
            appears, a 15-second confirmation window begins. All pools' arrival times are
            recorded relative to the first notification.</li>
          <li><strong>Full vs Empty Templates</strong> - Some pools send an empty template
            immediately (fast but contains no transactions) followed by a full template that
            includes transactions. A race is won by the first pool that delivers a full
            template. The "Any Template" view shows the first notification regardless of
            content, while "Full Template" (the default) only considers templates that
            include transactions.</li>
          <li><strong>Statistics</strong> - Median offsets are computed per pool across
            configurable time windows. Win counts and win percentages show which pools
            consistently deliver first.</li>
        </ul>
      </section>

      <section class="about-section">
        <h2>Vantage Points</h2>
        <p>
          A vantage point is a measurement location: a collector node deployed in a specific
          geographic region. Results vary by geography. A pool's server in Frankfurt will
          naturally appear faster to our European vantage than our US-based one.
        </p>
        <p>
          We deploy vantages in multiple locations to provide a balanced global view. The
          "All Vantages" view pools observations from all available vantage points together, while
          selecting a specific vantage shows only that location's measurements.
        </p>
      </section>

      <section class="about-section">
        <h2>Methodology Notes</h2>
        <ul>
          <li>Timing resolution is bounded by TCP delivery and event-loop scheduling (~0.1ms).
            Sub-millisecond differences in any single race should be treated as noise.</li>
          <li>Pool connections use throwaway Bitcoin addresses. No real mining occurs.</li>
          <li>Some pools require KYC or reject unauthorized connections. These are excluded
            from measurements when unavailable.</li>
          <li>Block miner attribution is looked up from mempool.space after the race completes
            and is provided for informational context only.</li>
        </ul>
      </section>

      <section class="about-section">
        <h2>For Pool Operators</h2>
        <p>
          If you would like your pool added to StratumRace, please open an issue on our
          <a href="https://github.com/proofofmike/stratum-race/issues" target="_blank" rel="noopener">GitHub repository</a>.
          Pools should have an established user base with a cumulative BTC hashrate of
          approximately 1+ PH/s to be considered for inclusion.
        </p>
        <p>
          If you are a pool operator and would like your pool removed, open an issue and we
          will take action. The StratumRace operators reserve the right to add and remove
          pools from the site at their discretion.
        </p>
      </section>

      <section class="about-section">
        <h2>Feedback &amp; Issues</h2>
        <p>
          Found a bug, have a question, or want to request a feature? Open an issue on our
          <a href="https://github.com/proofofmike/stratum-race/issues" target="_blank" rel="noopener">GitHub repository</a>.
        </p>
      </section>

      <section class="about-section">
        <h2>Project &amp; Contributors</h2>
        <p>
          StratumRace began as an original concept and Python CLI measurement tool created by
          <a href="https://proofofmike.com" target="_blank" rel="noopener">ProofOfMike</a>.
        </p>
        <p>
          <a href="https://x.com/OGBTC" target="_blank" rel="noopener">Marshall Long</a>
          helped bring the project to the web by creating the first browser-based implementation
          and contributing to its early development.
        </p>
        <p>
          The current platform was redesigned and rebuilt by
          <a href="https://proofofmike.com" target="_blank" rel="noopener">Mike</a>
          and
          <a href="https://atlaspool.io" target="_blank" rel="noopener">Matt</a>,
          expanding the original tool into the multi-vantage measurement and analytics platform
          available today.
        </p>
        <p>
          Follow the maintainers of StratumRace on X:
          <a href="https://x.com/proofofmike" target="_blank" rel="noopener">@proofofmike</a>
          and
          <a href="https://x.com/AtlasPool_io" target="_blank" rel="noopener">@AtlasPool_io</a>.
        </p>
      </section>

      <section class="about-section">
        <h2>Open Source</h2>
        <p>
          StratumRace is open source. The measurement collector and standalone web application
          are available on
          <a href="https://github.com/proofofmike/stratum-race" target="_blank" rel="noopener">GitHub</a>.
          Anyone can review the code, verify the methodology, contribute improvements, or run
          their own instance.
        </p>
      </section>
    </article>
  </div>
</template>

<style scoped>
.about-view {
  max-width: 800px;
  margin: 0 auto;
}

.about-content h1 {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 2rem;
}

.about-section {
  margin-bottom: 2rem;
}

.about-section h2 {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 0.75rem;
}

.about-section p {
  color: var(--text-primary);
  line-height: 1.7;
  margin-bottom: 0.75rem;
}

.about-section ul {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.about-section li {
  color: var(--text-primary);
  line-height: 1.7;
  padding-left: 1.25rem;
  position: relative;
}

.about-section li::before {
  content: '→';
  position: absolute;
  left: 0;
  color: var(--accent);
}

.about-section code {
  font-family: var(--font-mono);
  font-size: 0.875em;
  background: var(--surface-elevated);
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  color: var(--accent);
}

.about-section a {
  color: var(--accent);
  text-decoration: none;
}

.about-section a:hover {
  text-decoration: underline;
}
</style>
