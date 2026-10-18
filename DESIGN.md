# Echo dashboard design

## Scene and theme

A person setting up their assistant on a Mac in daylight, moving between familiar
services and wanting a clear next step. A light utility surface with a quiet green accent.

## Color

Restrained palette. Near-white canvas, a softly tinted sidebar,
dark green-gray text, a deep leaf-green action color. All tokens use OKLCH.
Consistent line icons accompany provider monograms; brand-associated colors stay inside icons.

## Typography

Native system sans. 14px body, 12px utility labels, 16px section headings,
36px page title. Weight and spacing establish hierarchy, not decorative display fonts.

## Layout

212px sidebar, open main content, a searchable provider list, and an adjacent
connection detail panel. Below 760px, navigation becomes horizontal and the provider catalog opens first.
Selecting an app replaces the list with its details; a back control returns to the catalog. No repeated feature-card grid.

## Components

Rounded buttons, full-width form controls, quiet dividers, labelled
status pills, provider monograms, inline errors, and a persistent connection flow
status. Connection details stay in the page rather than opening a modal.

## Motion

Brief color and opacity transitions. No decorative entrance animations.
Honor prefers-reduced-motion.
