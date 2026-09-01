"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ArrowIcon } from "@/components/icons/arrow-icon";
 import { SLIDES } from "../../data/landing-page-data";

const INTERVAL = 5000;

export function HeroSection() {
  const [current, setCurrent] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setCurrent((prev) => (prev + 1) % SLIDES.length);
    }, INTERVAL);
  };

  useEffect(() => {
    startTimer();
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const goTo = (index: number) => {
    setCurrent(index);
    startTimer(); // reset timer on manual click
  };

  return (
    <section className="hero-full" id="product">

      {/* Background image stack — each image fades in/out via opacity */}
      <div className="hero-bg" aria-hidden="true">
        {SLIDES.map((slide, i) => (
          <div
            key={slide.src}
            className={`hero-bg-slide${i === current ? " is-active" : ""}`}
          >
            <Image
              src={slide.src}
              alt={slide.alt}
              fill
              priority={i === 0}
              sizes="100vw"
              style={{ objectFit: "cover", objectPosition: "center" }}
            />
          </div>
        ))}
        <div className="hero-bg-overlay" />
      </div>

      {/* Centered text */}
      <div className="hero-center">
        <p className="hero-eyebrow">Listing intelligence · US brokerages</p>

        <h1>
          Not just leads
          <br />
          <span className="hero-accent">Leads that are ready to close.</span>
        </h1>

        <p className="hero-sub">
          Import your leads. Score every contact by property signals.
          Let AI open the conversation. Hand warm replies to your agents.
        </p>

        <div className="hero-actions">
          <Link className="button button-large hero-btn-primary" href="/register">
            Create an account
            <ArrowIcon />
          </Link>
          <button
            className="hero-btn-ghost"
            onClick={() =>
              document
                .getElementById("why-listingiq")
                ?.scrollIntoView({ behavior: "smooth", block: "start" })
            }
          >
            Why us?
          </button>
        </div>
      </div>

      {/* Dot indicators */}
      <div className="hero-dots" aria-label="Slide navigation">
        {SLIDES.map((_, i) => (
          <button
            key={i}
            type="button"
            className={`hero-dot${i === current ? " hero-dot-active" : ""}`}
            onClick={() => goTo(i)}
            aria-label={`Slide ${i + 1}`}
          />
        ))}
      </div>

    </section>
  );
}
